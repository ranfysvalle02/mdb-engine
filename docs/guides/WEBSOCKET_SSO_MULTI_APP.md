# WebSocket + SSO Multi-App Configuration Guide

Complete guide for configuring WebSocket connections in multi-app SSO deployments with proper CORS, CSRF, and authentication handling.

## Overview

When deploying multiple SSO apps with WebSocket support, you need to ensure:

1. ✅ **CSRF middleware** is configured on the parent app (automatically handled)
2. ✅ **CORS config** is properly merged from child apps to parent app
3. ✅ **WebSocket routes** are registered on parent app with full path prefixes
4. ✅ **Origin validation** works correctly for WebSocket upgrade requests
5. ✅ **Cookie-based authentication** - JWT tokens stored in httpOnly cookies

**Security Note**: MDB-Engine uses **httpOnly cookies** for WebSocket authentication. This method:
- Provides XSS protection (tokens not accessible to JavaScript)
- CSRF protection via Origin validation + SameSite cookies
- Avoids URL logging risks (token not in query params)
- Uses browser-native WebSocket API (cookies sent automatically)
- Provides secure token transmission

See [WebSocket Security Guide](./WEBSOCKET_SECURITY_MULTI_APP_SSO.md) for comprehensive security details.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│         Parent FastAPI App (Port 8000)                   │
│  • CSRF Middleware (validates WebSocket origins)        │
│  • CORS Middleware (merged from all child apps)         │
│  • WebSocket routes: /app1/ws, /app2/ws, /app3/ws       │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────┼───────┐
       │       │       │
       ▼       ▼       ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Auth Hub │ │  App 1   │ │  App 2   │
│ /auth-hub│ │  /app1   │ │  /app2   │
│          │ │          │ │          │
│ WebSocket│ │ WebSocket│ │ WebSocket│
│ /ws      │ │ /ws      │ │ /ws      │
└──────────┘ └──────────┘ └──────────┘
```

**Key Point**: WebSocket routes are registered on the **parent app**, so parent app middleware (CSRF) validates origins using the **parent app's merged CORS config**.

## Quick Setup Checklist

Before diving into details, ensure you have:

- [ ] **Backend**: Cookies set with `path="/"` during login (automatic in MDB-Engine)
- [ ] **Backend**: CORS configured with `allow_credentials: true`
- [ ] **Backend**: WebSocket endpoint defined in manifest
- [ ] **Backend**: CSRF middleware enabled (automatic for shared auth)
- [ ] **Frontend**: User authenticated before connecting WebSocket
- [ ] **Frontend**: WebSocket URL includes full path prefix (e.g., `/app1/ws`)

## Manifest Configuration

### 1. Auth Hub Manifest

```json
{
  "schema_version": "2.0",
  "slug": "auth-hub",
  "name": "SSO Auth Hub",
  "auth": {
    "mode": "shared",
    "roles": ["base_user", "viewer", "editor", "admin"],
    "default_role": "base_user",
    "require_role": "base_user",
    "public_routes": ["/", "/health", "/login", "/register", "/api/public"]
  },
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",
      "http://localhost:8000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  },
  "data_access": {
    "read_scopes": ["auth-hub"],
    "write_scope": "auth-hub"
  }
}
```

**Key Points:**
- ✅ `"mode": "shared"` enables SSO and CSRF middleware
- ✅ `"allow_credentials": true` is **REQUIRED** for cookie-based auth
- ✅ CORS origins must match your frontend domain exactly

### 2. SSO App with WebSocket

```json
{
  "schema_version": "2.0",
  "slug": "app1",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "auth_hub_url": "/auth-hub",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health"]
  },
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": true,
        "allow_anonymous": false
      },
      "ping_interval": 30
    }
  },
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",
      "http://localhost:8000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  },
  "data_access": {
    "read_scopes": ["app1"],
    "write_scope": "app1"
  }
}
```

**Key Points:**
- ✅ `websockets` section defines your WebSocket endpoint
- ✅ `auth.required: true` means users must be authenticated
- ✅ `cors.allow_credentials: true` is **REQUIRED** for cookies
- ✅ CORS origins are merged from all apps to parent app

### 3. Development (Wildcard Origins)

For development, you can use wildcard origins:

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

**⚠️ Warning**: Wildcard origins (`["*"]`) are **not recommended for production**. Use specific origins instead.

## Backend Setup

### Step 1: Ensure Cookies Are Set Correctly

**CRITICAL**: Cookies must be set with `path="/"` to work across mounted apps. MDB-Engine does this automatically, but verify your login endpoint uses `set_auth_cookies()`:

```python
from mdb_engine.auth.cookie_utils import set_auth_cookies
from mdb_engine.auth.utils import login_user

@app.post("/login")
async def login(request: Request, email: str, password: str):
    # ... validate credentials ...
    
    # Generate tokens
    access_token, refresh_token = generate_tokens(user)
    
    # Create response
    response = JSONResponse({"success": True})
    
    # Set cookies - path="/" is set automatically
    set_auth_cookies(
        response,
        access_token,
        refresh_token,
        request=request,
        config=config  # Your manifest auth config
    )
    
    return response
```

**What happens automatically:**
- ✅ Cookies are set with `path="/"` (required for multi-app)
- ✅ Cookies are `httpOnly=True` (XSS protection)
- ✅ Cookies are `secure=True` in production (HTTPS only)
- ✅ Cookies use `samesite="lax"` (CSRF protection)

### Step 2: Multi-App Setup

```python
from pathlib import Path
from mdb_engine import MongoDBEngine
import os

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "my_apps_db")
)

# Create multi-app
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
    title="My SSO Platform",
)

# Run with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

**What happens automatically:**
- ✅ CSRF middleware added to parent app (if any child uses shared auth)
- ✅ CORS configs merged from all child apps
- ✅ WebSocket routes registered on parent app with full path prefixes
- ✅ Origin validation uses merged CORS config

## What Happens Automatically

### 1. CSRF Middleware on Parent App

When any child app uses `"mode": "shared"`, CSRF middleware is **automatically added** to the parent app:

```python
# This happens automatically in create_multi_app()
if has_shared_auth:
    csrf_middleware = create_csrf_middleware(parent_csrf_config)
    parent_app.add_middleware(csrf_middleware)
```

### 2. CORS Config Merging

CORS configs from all child apps are **automatically merged** into the parent app:

```python
# Parent app gets merged CORS config
parent_app.state.cors_config = {
    "enabled": True,
    "allow_origins": [
        "http://localhost:3000",  # from app1
        "http://localhost:8000",  # from app2
        "https://yourdomain.com"  # from auth-hub
    ],
    "allow_credentials": True,
    # ... other merged settings
}
```

### 3. WebSocket Route Registration

WebSocket routes are **automatically registered** on the parent app with full path prefixes:

- Child app WebSocket `/ws` → Parent app route `/app1/ws`
- Child app WebSocket `/events` → Parent app route `/app2/events`

## Frontend Setup

### Step 1: Ensure User Is Authenticated

**CRITICAL**: User must be logged in before connecting WebSocket. The httpOnly cookie is set during login:

```typescript
// Login first - this sets the httpOnly cookie
async function login(email: string, password: string) {
  const response = await fetch('/auth-hub/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // IMPORTANT: sends cookies
    body: JSON.stringify({ email, password })
  });
  
  if (response.ok) {
    // Cookie is now set - can connect WebSocket
    return await response.json();
  }
  throw new Error('Login failed');
}

// After login, cookie is available for WebSocket
await login('user@example.com', 'password');
```

### Step 2: Connect WebSocket

**IMPORTANT**: MDB-Engine uses **httpOnly cookies** for WebSocket authentication. The browser automatically sends cookies on WebSocket upgrade requests - no token needed in JavaScript!

```typescript
// Connect to WebSocket - browser automatically sends httpOnly cookies
// CRITICAL: Include full path prefix (e.g., /app1/ws, not /ws)
const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/app1/ws`;

// No token needed - browser automatically sends httpOnly cookies
const ws = new WebSocket(wsUrl);

ws.onopen = () => {
  console.log('✅ WebSocket connected');
  ws.send(JSON.stringify({ type: 'ping' }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('📨 Received:', data);
};

ws.onerror = (error) => {
  console.error('❌ WebSocket error:', error);
  // Common errors:
  // - 403: CSRF/Origin validation failed or missing cookie
  // - 1008: Authentication failed (invalid/expired token)
};

ws.onclose = (event) => {
  console.log('🔌 WebSocket closed', {
    code: event.code,
    reason: event.reason,
    wasClean: event.wasClean
  });
  
  // Reconnect logic if needed
  if (event.code === 1008) {
    // Auth failure - may need to refresh token
    console.warn('Authentication failed - may need to refresh token');
  }
};
```

**Key Points:**
- ✅ **No token in JavaScript** - browser sends httpOnly cookie automatically
- ✅ **Include path prefix** - use `/app1/ws`, not `/ws`
- ✅ **Use `credentials: 'include'`** - for login requests (ensures cookies are sent/received)
- ✅ **Handle errors** - 403 = CSRF/Origin issue, 1008 = auth failure

**Why Cookie-Based Authentication?**
- ✅ XSS protection (tokens not accessible to JavaScript)
- ✅ CSRF protection via Origin validation + SameSite cookies
- ✅ Avoids URL logging risks (token not in query params)
- ✅ Browser-native support (cookies sent automatically)
- ✅ Secure token transmission (httpOnly cookies)

### React Hook Example

```typescript
import { useEffect, useRef, useState } from 'react';

function useWebSocket(appSlug: string, endpoint: string = 'ws') {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<any[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/${appSlug}/${endpoint}`;
    
    // No token needed - browser automatically sends httpOnly cookies
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setConnected(true);
      wsRef.current = ws;
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages(prev => [...prev, data]);
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
    };

    return () => {
      ws.close();
    };
  }, [appSlug, endpoint]);

  const sendMessage = (message: any) => {
    if (wsRef.current && connected) {
      wsRef.current.send(JSON.stringify(message));
    }
  };

  return { connected, messages, sendMessage };
}

// Usage
function MyComponent() {
  const { connected, messages, sendMessage } = useWebSocket('app1', 'ws');

  return (
    <div>
      <p>Status: {connected ? 'Connected' : 'Disconnected'}</p>
      <button onClick={() => sendMessage({ type: 'ping' })}>
        Send Ping
      </button>
      <ul>
        {messages.map((msg, i) => (
          <li key={i}>{JSON.stringify(msg)}</li>
        ))}
      </ul>
    </div>
  );
}
```

## Authentication Flow

### WebSocket Authentication: Cookie-Based

MDB-Engine uses **httpOnly cookies** to securely store and transmit JWT tokens. This method:

- ✅ **XSS Protection** - Tokens not accessible to JavaScript
- ✅ **CSRF Protection** - Origin validation + SameSite cookies
- ✅ **Avoids URL logging** - Token not in query params (security best practice)
- ✅ **Browser-native** - Cookies automatically sent on WebSocket upgrade requests
- ✅ **Secure** - Token transmitted via httpOnly cookies

**Client Implementation:**

```javascript
// No token needed - browser automatically sends httpOnly cookies
// Ensure httpOnly cookie is set during authentication/login
const ws = new WebSocket('wss://example.com/app1/ws');

ws.onopen = () => {
  console.log('WebSocket connected and authenticated');
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
  // Check server logs for authentication failure details
};
```

**How It Works:**

1. Client sends WebSocket upgrade request (browser automatically includes httpOnly cookies)
2. Server extracts token from cookie **before** accepting connection
3. Server validates CSRF cookie presence (if authenticated)
4. Server validates Origin header (CSRF protection)
5. Server validates JWT token from httpOnly cookie
6. If valid, server accepts connection
7. If invalid, connection is rejected (no accept() called)

**Backend Authentication:**

Authentication happens automatically in MDB-Engine. The `authenticate_websocket()` function:

- Extracts token from httpOnly cookie
- Validates JWT token using shared secret
- Returns user information if valid
- Rejects connection if invalid (before accept())

No additional backend code needed - it's handled automatically!

## CORS Configuration Best Practices

### Development

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true
  }
}
```

### Production

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "https://yourdomain.com",
      "https://app.yourdomain.com",
      "https://admin.yourdomain.com"
    ],
    "allow_credentials": true,
    "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"],
    "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
    "max_age": 3600
  }
}
```

## Common Setup Issues & Solutions

### Issue 1: 403 Forbidden on WebSocket Connection

**Symptoms:**
- WebSocket connection fails immediately
- Browser console shows 403 error
- Server logs show "Invalid origin" or "CSRF token missing"

**Causes & Solutions:**

1. **CORS Origin Not Allowed**
   ```json
   // ❌ WRONG: Missing frontend origin
   "cors": {
     "allow_origins": ["https://yourdomain.com"]
   }
   
   // ✅ CORRECT: Include your frontend origin
   "cors": {
     "allow_origins": [
       "http://localhost:3000",  // Your frontend
       "https://yourdomain.com"
     ],
     "allow_credentials": true
   }
   ```

2. **Missing `allow_credentials`**
   ```json
   // ❌ WRONG: Cookies won't be sent
   "cors": {
     "allow_origins": ["*"],
     "allow_credentials": false
   }
   
   // ✅ CORRECT: Cookies sent with requests
   "cors": {
     "allow_origins": ["http://localhost:3000"],
     "allow_credentials": true
   }
   ```

3. **Wrong WebSocket URL**
   ```typescript
   // ❌ WRONG: Missing path prefix
   const ws = new WebSocket('ws://localhost:8000/ws');
   
   // ✅ CORRECT: Include full path prefix
   const ws = new WebSocket('ws://localhost:8000/app1/ws');
   ```

**Debug Steps:**
```python
# Check parent app's CORS config
print("CORS Origins:", app.state.cors_config["allow_origins"])
print("Allow Credentials:", app.state.cors_config["allow_credentials"])

# Check WebSocket routes
for route in app.routes:
    if hasattr(route, 'path') and 'ws' in route.path:
        print(f"WebSocket Route: {route.path}")
```

### Issue 2: Authentication Fails (1008 Error)

**Symptoms:**
- WebSocket connects then immediately closes
- Error code 1008 (Policy Violation)
- Server logs show "No mdb_auth_token cookie found" or "No token cookie found"

**Causes & Solutions:**

1. **Cookie Not Set During Login**
   ```python
   # ✅ CORRECT: Use set_auth_cookies() helper
   from mdb_engine.auth.cookie_utils import set_auth_cookies
   
   response = JSONResponse({"success": True})
   set_auth_cookies(response, access_token, refresh_token, request=request)
   ```

2. **Cookie Path Issue (Multi-App)**
   ```python
   # ✅ CORRECT: path="/" is set automatically by set_auth_cookies()
   # Verify in browser DevTools → Application → Cookies
   # Cookie should show: Path: /
   ```

3. **Token Expired**
   ```typescript
   // Refresh token before connecting
   async function refreshToken() {
     const response = await fetch('/auth-hub/refresh', {
       method: 'POST',
       credentials: 'include'
     });
     return response.ok;
   }
   
   // Connect after refresh
   await refreshToken();
   const ws = new WebSocket('ws://localhost:8000/app1/ws');
   ```

**Debug Steps:**
```javascript
// Check cookies in browser console
console.log('Cookies:', document.cookie);

// Check in DevTools → Application → Cookies
// Should see:
// - token: <jwt-token> (httpOnly: true, Path: /)
// - csrf_token: <csrf-token> (httpOnly: false, Path: /)
```

### Issue 3: WebSocket Route Not Found (404)

**Symptoms:**
- WebSocket connection fails with 404
- Route doesn't exist

**Solutions:**
1. ✅ Verify `websockets` section exists in manifest
2. ✅ Check that app was mounted successfully (check startup logs)
3. ✅ Ensure path prefix matches WebSocket URL

**Debug:**
```python
# List all WebSocket routes
for route in app.routes:
    if hasattr(route, 'path'):
        route_type = 'WS' if not hasattr(route, 'methods') else route.methods
        print(f"{route_type}: {route.path}")
```

### Issue 4: CSRF Cookie Missing

**Symptoms:**
- 403 error with "CSRF token missing" message
- Works in some browsers but not others

**Solutions:**
1. **Get CSRF Cookie First**
   ```typescript
   // Make a GET request to get CSRF cookie
   await fetch('http://localhost:8000/', {
     credentials: 'include'
   });
   
   // Now WebSocket connection will have CSRF cookie
   const ws = new WebSocket('ws://localhost:8000/app1/ws');
   ```

2. **Verify CSRF Cookie Exists**
   ```javascript
   // Check in DevTools → Application → Cookies
   // Should see: csrf_token cookie
   ```

## Complete Example

See the full working example in:
- `examples/advanced/sso-multi-app/`
- `examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json` (has WebSocket config)

## Security Best Practices

### Cookie-Based Authentication

**✅ DO:**
- Use httpOnly cookies for token storage (set server-side)
- Ensure cookies have `path="/"` for multi-app compatibility
- Validate token on server before accepting connection
- Use HTTPS/WSS in production
- Implement token refresh for long-lived connections
- Ensure CORS `allow_credentials: true` is set

**❌ DON'T:**
- Put tokens in URL query params (logging risk)
- Store tokens in localStorage/sessionStorage (XSS risk)
- Accept connections before validating tokens
- Use wildcard CORS origins in production
- Forget to set `path="/"` on cookies (breaks multi-app setups)

### Token Management

- Store tokens in httpOnly cookies (set server-side during login)
- Cookies automatically sent by browser on WebSocket upgrade requests
- Implement token refresh before expiration (server-side cookie refresh)
- Handle token expiration gracefully (reconnect after refresh)
- Never log or expose tokens in client-side code

## Complete Setup Checklist

### Backend Checklist

- [ ] **Manifest Configuration**
  - [ ] `auth.mode: "shared"` for SSO apps
  - [ ] `websockets` section defined with endpoint path
  - [ ] `cors.enabled: true`
  - [ ] `cors.allow_credentials: true` (REQUIRED)
  - [ ] `cors.allow_origins` includes your frontend domain

- [ ] **Cookie Setup**
  - [ ] Login endpoint uses `set_auth_cookies()` helper
  - [ ] Cookies automatically set with `path="/"` (for multi-app)
  - [ ] Cookies are `httpOnly=True` (XSS protection)

- [ ] **Multi-App Setup**
  - [ ] Apps mounted with `create_multi_app()`
  - [ ] Path prefixes start with `/` (e.g., `/app1`)
  - [ ] CSRF middleware automatically added (if shared auth)

### Frontend Checklist

- [ ] **Authentication**
  - [ ] User logs in before connecting WebSocket
  - [ ] Login request uses `credentials: 'include'`
  - [ ] Cookie is set (check DevTools → Cookies)

- [ ] **WebSocket Connection**
  - [ ] WebSocket URL includes full path prefix (`/app1/ws`)
  - [ ] No token passed manually (browser sends cookie)
  - [ ] Error handling for 403 (CSRF/Origin) and 1008 (Auth)

- [ ] **CORS Configuration**
  - [ ] Frontend origin matches CORS `allow_origins`
  - [ ] All API requests use `credentials: 'include'`

## Key Takeaways

1. ✅ **CSRF middleware is automatically added** to parent app when child apps use shared auth
2. ✅ **CORS configs are automatically merged** from all child apps to parent app
3. ✅ **WebSocket routes are registered** on parent app with full path prefixes
4. ✅ **Origin validation** uses parent app's merged CORS config
5. ✅ **Cookie-based authentication** - Browser automatically sends httpOnly cookies
6. ✅ **Cookies use `path="/"`** automatically - works with mounted apps
7. ✅ **Always include CORS config** in each child app's manifest
8. ✅ **Use specific origins in production**, not wildcards
9. ✅ **WebSocket URLs must include path prefix**: `/app1/ws`, not `/ws`
10. ✅ **Ensure `allow_credentials: true`** in CORS config for cookie support

---

**Related Documentation:**
- [WebSocket Security Guide](./WEBSOCKET_SECURITY_MULTI_APP_SSO.md) - **Comprehensive security guide**
- [WebSocket Troubleshooting Guide](./WEBSOCKET_TROUBLESHOOTING.md) - **Start here if you're having connection issues!**
- [SSO Multi-App Setup Guide](./SSO_MULTI_APP_SETUP.md)
- [WebSocket Routing README](../../mdb_engine/routing/README.md)
- [SSO Multi-App Cheat Sheet](../api/SSO_MULTI_APP_CHEATSHEET.md)
