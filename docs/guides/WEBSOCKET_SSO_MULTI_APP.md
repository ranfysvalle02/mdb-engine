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

## Multi-App Setup

### Python Code

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

## Frontend WebSocket Connection

### JavaScript/TypeScript

**IMPORTANT**: MDB-Engine uses **httpOnly cookies** for WebSocket authentication. The browser automatically sends cookies on WebSocket upgrade requests - no token needed in JavaScript!

```typescript
// Connect to WebSocket - browser automatically sends httpOnly cookies
const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/app1/ws`;

// No token needed - browser automatically sends httpOnly cookies
const ws = new WebSocket(wsUrl);

ws.onopen = () => {
  console.log('WebSocket connected');
  ws.send(JSON.stringify({ type: 'ping' }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Received:', data);
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = () => {
  console.log('WebSocket closed');
};
```

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

## Troubleshooting

### 403 Forbidden on WebSocket Connection

**Cause**: Origin validation failed (CSRF middleware rejected the request)

**Solutions**:
1. ✅ Ensure CORS config includes your frontend origin
2. ✅ Check that `allow_credentials: true` is set
3. ✅ Verify WebSocket URL uses correct path prefix (`/app1/ws`, not `/ws`)
4. ✅ Check browser console for CORS errors

**Debug**:
```python
# Check parent app's CORS config
print(app.state.cors_config["allow_origins"])
```

### WebSocket Route Not Found (404)

**Cause**: WebSocket route not registered on parent app

**Solutions**:
1. ✅ Verify `websockets` section exists in manifest
2. ✅ Check that app was mounted successfully (check logs)
3. ✅ Ensure path prefix matches WebSocket URL (`/app1/ws`)

**Debug**:
```python
# List all routes
for route in app.routes:
    if hasattr(route, 'path'):
        print(f"{route.methods if hasattr(route, 'methods') else 'WS'}: {route.path}")
```

### Authentication Fails

**Cause**: Cookie not sent or invalid token

**Solutions**:
1. ✅ Ensure httpOnly cookie is set during login/authentication
2. ✅ Check browser DevTools → Application → Cookies to verify cookie exists
3. ✅ Verify cookie has `path="/"` (required for multi-app setups)
4. ✅ Ensure CORS `allow_credentials: true` is set
5. ✅ Verify JWT secret matches across all apps
6. ✅ Check token expiration
7. ✅ Verify CSRF cookie is present (if authenticated)

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

## Key Takeaways

1. ✅ **CSRF middleware is automatically added** to parent app when child apps use shared auth
2. ✅ **CORS configs are automatically merged** from all child apps to parent app
3. ✅ **WebSocket routes are registered** on parent app with full path prefixes
4. ✅ **Origin validation** uses parent app's merged CORS config
5. ✅ **Cookie-based authentication** - Browser automatically sends httpOnly cookies
6. ✅ **Always include CORS config** in each child app's manifest
7. ✅ **Use specific origins in production**, not wildcards
8. ✅ **WebSocket URLs must include path prefix**: `/app1/ws`, not `/ws`
9. ✅ **Ensure `allow_credentials: true`** in CORS config for cookie support

---

**Related Documentation:**
- [WebSocket Security Guide](./WEBSOCKET_SECURITY_MULTI_APP_SSO.md) - **Comprehensive security guide**
- [WebSocket Troubleshooting Guide](./WEBSOCKET_TROUBLESHOOTING.md) - **Start here if you're having connection issues!**
- [SSO Multi-App Setup Guide](./SSO_MULTI_APP_SETUP.md)
- [WebSocket Routing README](../../mdb_engine/routing/README.md)
- [SSO Multi-App Cheat Sheet](../api/SSO_MULTI_APP_CHEATSHEET.md)
