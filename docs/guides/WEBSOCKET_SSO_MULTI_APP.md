# WebSocket + SSO Multi-App Configuration Guide

Complete guide for configuring WebSocket connections in multi-app SSO deployments with proper CORS, CSRF, and authentication handling.

## Overview

When deploying multiple SSO apps with WebSocket support, you need to ensure:

1. ✅ **CSRF middleware** is configured on the parent app (automatically handled)
2. ✅ **CORS config** is properly merged from child apps to parent app
3. ✅ **WebSocket routes** are registered on parent app with full path prefixes
4. ✅ **Origin validation** works correctly for WebSocket upgrade requests

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

```typescript
// Connect to WebSocket with proper origin
const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/app1/ws`;

// Include auth token in query params or headers
const token = getCookie('mdb_auth_token'); // Get from cookie
const ws = new WebSocket(`${wsUrl}?token=${token}`);

// Or use Authorization header (if supported by your setup)
// Note: WebSocket API doesn't support custom headers directly
// Use query params or cookie-based auth instead

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

### React Hook Example

```typescript
import { useEffect, useRef, useState } from 'react';

function useWebSocket(appSlug: string, endpoint: string = 'ws') {
  const [connected, setConnected] = useState(false);
  const [messages, setMessages] = useState<any[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = document.cookie
      .split('; ')
      .find(row => row.startsWith('mdb_auth_token='))
      ?.split('=')[1];
    
    const wsUrl = `${protocol}//${window.location.host}/${appSlug}/${endpoint}?token=${token}`;
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

### WebSocket Authentication Options

1. **Query Parameter** (Recommended for WebSocket):
   ```javascript
   const ws = new WebSocket(`wss://example.com/app1/ws?token=${token}`);
   ```

2. **Cookie-Based** (Automatic if using SSO):
   ```javascript
   // Cookie is automatically sent with WebSocket upgrade request
   const ws = new WebSocket('wss://example.com/app1/ws');
   ```

3. **After Connection** (Send auth message):
   ```javascript
   ws.onopen = () => {
     ws.send(JSON.stringify({ type: 'auth', token: token }));
   };
   ```

### Backend Authentication Handler

```python
from mdb_engine.routing.websockets import register_message_handler

# Register auth handler
@register_message_handler("app1", "realtime", "auth")
async def handle_auth(websocket, message: dict):
    """Handle authentication message."""
    token = message.get("token")
    if not token:
        await websocket.send_json({
            "type": "error",
            "message": "Token required"
        })
        return
    
    # Validate token using shared user pool
    from mdb_engine import get_shared_user_pool
    # Note: You'll need to access the request/app context
    # This is a simplified example
    user = await validate_token(token)
    
    if user:
        await websocket.send_json({
            "type": "auth_success",
            "user": user["email"]
        })
    else:
        await websocket.send_json({
            "type": "auth_failed",
            "message": "Invalid token"
        })
```

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

**Cause**: Token not sent or invalid

**Solutions**:
1. ✅ Include token in query params: `?token=${token}`
2. ✅ Ensure cookie is set and sent (check browser DevTools)
3. ✅ Verify JWT secret matches across all apps
4. ✅ Check token expiration

## Complete Example

See the full working example in:
- `examples/advanced/sso-multi-app/`
- `examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json` (has WebSocket config)

## Key Takeaways

1. ✅ **CSRF middleware is automatically added** to parent app when child apps use shared auth
2. ✅ **CORS configs are automatically merged** from all child apps to parent app
3. ✅ **WebSocket routes are registered** on parent app with full path prefixes
4. ✅ **Origin validation** uses parent app's merged CORS config
5. ✅ **Always include CORS config** in each child app's manifest
6. ✅ **Use specific origins in production**, not wildcards
7. ✅ **WebSocket URLs must include path prefix**: `/app1/ws`, not `/ws`

---

**Related Documentation:**
- [SSO Multi-App Setup Guide](./SSO_MULTI_APP_SETUP.md)
- [WebSocket Routing README](../../mdb_engine/routing/README.md)
- [SSO Multi-App Cheat Sheet](../api/SSO_MULTI_APP_CHEATSHEET.md)
