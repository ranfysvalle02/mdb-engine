# WebSocket Troubleshooting Guide: Multi-App SSO & Authentication

Complete troubleshooting guide for WebSocket connections in multi-app SSO deployments, including common errors, debugging techniques, and solutions.

## Quick Reference: Common Issues

| Error | Code | Cause | Quick Fix |
|-------|------|-------|-----------|
| Connection fails immediately | 403 | CORS origin not allowed | Add frontend origin to `cors.allow_origins` |
| Connection fails immediately | 403 | Missing `allow_credentials` | Set `cors.allow_credentials: true` |
| Connection fails immediately | 403 | Wrong WebSocket URL | Use `/app1/ws` not `/ws` |
| Connects then closes | 1008 | No auth cookie | Ensure user is logged in first |
| Connects then closes | 1008 | Expired token | Refresh token before connecting |
| Connects then closes | 1008 | Missing CSRF cookie | Make GET request first to get CSRF cookie |
| Route not found | 404 | WebSocket not in manifest | Add `websockets` section to manifest |

## Table of Contents

- [Quick Setup Checklist](#quick-setup-checklist)
- [Common WebSocket Errors](#common-websocket-errors)
- [Error Code 1006: Abnormal Closure](#error-code-1006-abnormal-closure)
- [403 Forbidden Errors](#403-forbidden-errors)
- [CORS & Origin Issues](#cors--origin-issues)
- [Authentication Failures](#authentication-failures)
- [Debugging Checklist](#debugging-checklist)
- [Frontend Implementation Guide](#frontend-implementation-guide)
- [Backend Configuration Checklist](#backend-configuration-checklist)
- [Server-Side Debugging](#server-side-debugging)
- [Client-Side Debugging](#client-side-debugging)

---

## Quick Setup Checklist

Before troubleshooting, verify these basics:

### Backend ✅
- [ ] Manifest has `websockets` section with endpoint path
- [ ] Manifest has `cors.enabled: true` and `cors.allow_credentials: true`
- [ ] CORS `allow_origins` includes your frontend domain
- [ ] Login endpoint uses `set_auth_cookies()` helper
- [ ] Multi-app uses `create_multi_app()` with proper path prefixes

### Frontend ✅
- [ ] User is logged in before connecting WebSocket
- [ ] Login request uses `credentials: 'include'`
- [ ] WebSocket URL includes full path prefix (`/app1/ws`)
- [ ] No token passed manually (browser sends cookie automatically)
- [ ] Error handling for 403 and 1008 errors

---

## Common WebSocket Errors

### Error Code 1006: Abnormal Closure

**Symptoms:**
```
WebSocket connection to 'ws://localhost:8000/app-3/ws' failed
❌ WebSocket error: Event {type: 'error', ...}
🔌 Memory WebSocket disconnected {code: 1006, reason: 'No reason provided', wasClean: false}
```

**What it means:**
- Connection closed abnormally without a close frame
- Usually indicates a server-side rejection or network issue
- **NOT** a client-side error - the server is rejecting the connection

**Common Causes:**
1. ❌ **CSRF middleware rejecting origin** (most common)
2. ❌ **CORS config missing or incorrect**
3. ❌ **Authentication middleware rejecting connection**
4. ❌ **WebSocket route not registered on parent app**
5. ❌ **Server not running or wrong port**

---

## Error Code 1006: Abnormal Closure

### Diagnosis Steps

#### 1. Check Server Logs

Look for these log messages in your server output:

```bash
# Good signs (connection accepted):
🔌 WebSocket connection attempt for app 'app-3' (require_auth=True)
✅ WebSocket connected successfully

# Bad signs (connection rejected):
WebSocket upgrade rejected - invalid Origin: http://localhost:3000
CSRF token missing
Invalid origin for WebSocket connection
```

#### 2. Verify CORS Configuration

**Check parent app's CORS config:**

```python
# In your multi-app setup, after mounting apps:
print("Parent CORS config:", app.state.cors_config)
# Should show:
# {
#   "enabled": True,
#   "allow_origins": ["http://localhost:3000", ...],
#   "allow_credentials": True,  # CRITICAL for SSO
#   ...
# }
```

**Check child app manifest:**

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"],
    "allow_credentials": true,  // REQUIRED for cookie-based auth
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

#### 3. Verify CSRF Middleware

**Check if CSRF middleware is on parent app:**

```python
# After creating multi-app:
middleware_stack = app.user_middleware
for middleware in middleware_stack:
    print(f"Middleware: {middleware}")
# Should include CSRFMiddleware if any child app uses shared auth
```

**Check CSRF origin validation:**

The CSRF middleware validates WebSocket origins using `parent_app.state.cors_config`. If this is missing or incorrect, connections will be rejected with 1006.

#### 4. Verify WebSocket Route Registration

**Check if WebSocket route exists:**

```python
# List all routes on parent app
for route in app.routes:
    if hasattr(route, 'path'):
        print(f"Route: {route.path}")
# Should include: /app-3/ws (with path prefix)
```

**Expected output:**
```
Route: /health
Route: /app-3/ws  ✅ This should exist
Route: /app-3/... (other routes)
```

---

## 403 Forbidden Errors

### Symptoms

```
WebSocket connection failed: 403 Forbidden
```

### Solutions

#### 1. Fix CORS Configuration

**Problem:** Parent app's CORS config doesn't include your frontend origin.

**Solution:**

```json
// In child app manifest.json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",  // Your frontend origin
      "http://localhost:8000",  // Backend origin (if needed)
      "https://yourdomain.com"   // Production origin
    ],
    "allow_credentials": true,  // CRITICAL for SSO
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

#### 2. Verify CSRF Middleware Configuration

**Problem:** CSRF middleware is rejecting WebSocket upgrade requests.

**Solution:**

Ensure CSRF middleware is configured correctly on parent app:

```python
# This happens automatically when child apps use shared auth
# But verify it's working:

# Check parent app state
assert hasattr(app.state, "cors_config")
assert app.state.cors_config.get("allow_credentials") is True
```

#### 3. Check Origin Header

**Problem:** Browser isn't sending Origin header, or it doesn't match allowed origins.

**Debug:**

```javascript
// In browser console, before connecting:
console.log("Current origin:", window.location.origin);
// Should match one of your allowed origins

// Check if cookies are being sent:
console.log("Cookies:", document.cookie);
// Should include mdb_auth_token or similar
```

---

## CORS & Origin Issues

### Common Mistakes

#### ❌ Mistake 1: Wildcard Origin with Credentials

```json
// WRONG - Browsers reject this combination
{
  "cors": {
    "allow_origins": ["*"],
    "allow_credentials": true  // ❌ Not allowed with wildcard
  }
}
```

**Fix:** Use specific origins:

```json
{
  "cors": {
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true  // ✅ OK with specific origins
  }
}
```

#### ❌ Mistake 2: Missing Path Prefix in WebSocket URL

```javascript
// WRONG - Missing app path prefix
const ws = new WebSocket('ws://localhost:8000/ws');

// CORRECT - Include path prefix
const ws = new WebSocket('ws://localhost:8000/app-3/ws');
```

#### ❌ Mistake 3: Wrong Protocol (ws vs wss)

```javascript
// Development (HTTP)
const protocol = 'ws:';
const wsUrl = `${protocol}//localhost:8000/app-3/ws`;

// Production (HTTPS)
const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const wsUrl = `${protocol}//${window.location.host}/app-3/ws`;
```

---

## Authentication Failures

### Cookie-Based Authentication (MDB-Engine Standard)

MDB-Engine uses **httpOnly cookies** for WebSocket authentication. Tokens are stored in httpOnly cookies set during login, and the browser automatically sends them on WebSocket upgrade requests.

#### Problem: No Token Cookie

**Symptoms:**
- WebSocket connection rejected immediately
- Server logs show: "No token cookie found" or "No mdb_auth_token cookie found"
- Connection closes with code 1008 (Policy Violation)

**Solutions:**

1. **Verify Cookie is Set During Login:**
   ```javascript
   // ✅ CORRECT: Cookie set server-side during authentication
   // No client-side code needed - browser sends automatically
   const ws = new WebSocket('ws://localhost:8000/app-3/ws');
   
   // ❌ WRONG: Don't use query params
   const ws = new WebSocket(`ws://localhost:8000/app-3/ws?token=${token}`);
   
   // ❌ WRONG: Don't try to pass token manually
   const ws = new WebSocket('ws://localhost:8000/app-3/ws', [token]);
   ```

2. **Check Cookie in Browser DevTools:**
   ```javascript
   // Check browser DevTools → Application → Cookies
   // Verify "mdb_auth_token" cookie exists with:
   // - HttpOnly: true
   // - Secure: true (in production)
   // - SameSite: Lax
   // - Path: /
   ```

3. **Verify Authentication Completed:**
   ```javascript
   // Ensure user is logged in before connecting WebSocket
   // Cookie should be set during login/authentication flow
   // Check Network tab → Login request → Response Headers → Set-Cookie
   ```

#### Problem: Invalid Token

**Symptoms:**
- Connection rejected immediately
- Server logs show: "JWT decode error" or "Invalid token"
- Connection closes with code 1008

**Solutions:**

1. **Verify Cookie Contains Valid Token:**
   - Check browser DevTools → Application → Cookies → "mdb_auth_token"
   - Verify cookie value is a valid JWT (three parts separated by dots)
   - Ensure cookie wasn't corrupted or modified

2. **Check Token Expiration:**
   - Server validates token expiration automatically
   - If expired, refresh token via `/auth/refresh` endpoint
   - New cookie will be set automatically

3. **Verify JWT Secret Matches:**
   - Ensure backend uses correct JWT secret
   - Check environment variables are set correctly
   - Verify token was issued by same auth system

#### Problem: Token Expired

**Symptoms:**
- Initial connection works, then disconnects
- Server logs show "Signature has expired"
- Connection closes unexpectedly

**Solutions:**

1. **Refresh Token Before Connecting:**
   ```javascript
   async function connectWebSocket() {
     // Refresh token if needed (server handles cookie refresh)
     const response = await fetch('/auth/refresh', {
       method: 'POST',
       credentials: 'include' // Important: sends cookies
     });
     
     if (response.ok) {
       // New token cookie set automatically
       // Connect WebSocket - browser sends new cookie
       const ws = new WebSocket('ws://localhost:8000/app-3/ws');
       return ws;
     }
   }
   ```

2. **Handle Reconnection with Token Refresh:**
   ```javascript
   ws.onclose = async (event) => {
     if (event.code === 1008) {
       // Authentication failure - refresh token and reconnect
       console.log('Auth failed, refreshing token...');
       await fetch('/auth/refresh', {
         method: 'POST',
         credentials: 'include'
       });
       setTimeout(() => connectWebSocket(), 2000);
     }
   };
   ```

3. **Implement Proactive Token Refresh:**
   ```javascript
   // Monitor and refresh token proactively
   setInterval(async () => {
     const response = await fetch('/auth/refresh', {
       method: 'POST',
       credentials: 'include'
     });
     if (response.ok) {
       // New cookie set automatically
       // WebSocket will use new cookie on next connection
     }
   }, 10 * 60 * 1000); // Refresh every 10 minutes
   ```

#### Problem: CSRF Cookie Missing

**Symptoms:**
- Connection rejected with 403 Forbidden
- Server logs show: "CSRF token missing for WebSocket authentication"
- Connection closes immediately

**Solutions:**

1. **Ensure CSRF Cookie is Set:**
   - CSRF cookie is set automatically on first GET request
   - Make a GET request to any endpoint before connecting WebSocket
   - Check browser DevTools → Cookies → "csrf_token" exists

2. **Verify Cookie Path:**
   - CSRF cookie should have `path="/"`
   - Ensures cookie is available to all mounted apps
   - Check cookie attributes in browser DevTools

---

## Debugging Checklist

### Backend Checklist

- [ ] **Parent app has CORS config:**
  ```python
  assert hasattr(app.state, "cors_config")
  assert app.state.cors_config["enabled"] is True
  ```

- [ ] **CORS config includes frontend origin:**
  ```python
  assert "http://localhost:3000" in app.state.cors_config["allow_origins"]
  ```

- [ ] **allow_credentials is True:**
  ```python
  assert app.state.cors_config["allow_credentials"] is True
  ```

- [ ] **CSRF middleware is on parent app:**
  ```python
  # Check middleware stack
  csrf_found = any("CSRF" in str(m) for m in app.user_middleware)
  assert csrf_found, "CSRF middleware should be on parent app"
  ```

- [ ] **WebSocket route registered:**
  ```python
  ws_routes = [r for r in app.routes if "/app-3/ws" in str(r.path)]
  assert len(ws_routes) > 0, "WebSocket route not found"
  ```

- [ ] **Server logs show connection attempts:**
  ```bash
  # Look for:
  🔌 WebSocket connection attempt for app 'app-3'
  ```

### Frontend Checklist

- [ ] **WebSocket URL includes path prefix:**
  ```javascript
  const wsUrl = `ws://localhost:8000/app-3/ws`;  // ✅ Correct
  // NOT: ws://localhost:8000/ws  ❌
  ```

- [ ] **Protocol matches (ws for HTTP, wss for HTTPS):**
  ```javascript
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ```

- [ ] **Token is available and valid:**
  ```javascript
  const token = getAuthToken();
  if (!token) {
    console.error('No token available - authenticate first');
  }
  // Check token expiration
  const payload = JSON.parse(atob(token.split('.')[1]));
  console.log('Token expires:', new Date(payload.exp * 1000));
  ```

- [ ] **Cookie is set and sent automatically:**
  ```javascript
  // ✅ Correct: Browser automatically sends httpOnly cookies
  const ws = new WebSocket(url);
  // NOT: new WebSocket(`${url}?token=${token}`) ❌
  // NOT: new WebSocket(url, [token]) ❌
  ```

- [ ] **Origin matches allowed origins:**
  ```javascript
  console.log("Origin:", window.location.origin);
  // Should match one of: http://localhost:3000, etc.
  ```

---

## Frontend Implementation Guide

### Complete WebSocket Connection Example

```typescript
interface WebSocketConfig {
  appSlug: string;
  endpoint?: string;
  onMessage?: (data: any) => void;
  onError?: (error: Event) => void;
  onClose?: (event: CloseEvent) => void;
  maxReconnectAttempts?: number;
  reconnectDelay?: number;
}

class SSOWebSocketClient {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private config: Required<WebSocketConfig>;

  constructor(config: WebSocketConfig) {
    this.config = {
      endpoint: 'ws',
      maxReconnectAttempts: 5,
      reconnectDelay: 2000,
      onMessage: () => {},
      onError: () => {},
      onClose: () => {},
      ...config,
    };
  }

  connect(): void {
    // Determine protocol (ws for HTTP, wss for HTTPS)
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/${this.config.appSlug}/${this.config.endpoint}`;

    // No token needed - browser automatically sends httpOnly cookies
    // Ensure user is authenticated and cookie is set before connecting
    console.log(`🔌 Connecting to WebSocket: ${wsUrl}`);
    console.log(`   Current origin: ${window.location.origin}`);

    try {
      // Browser automatically sends httpOnly cookies
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log('✅ WebSocket connected');
        this.reconnectAttempts = 0;
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          console.log('📨 Received:', data);
          this.config.onMessage(data);
        } catch (error) {
          console.error('❌ Failed to parse message:', error);
        }
      };

      this.ws.onerror = (error) => {
        console.error('❌ WebSocket error:', error);
        console.log(`   Socket state: ${this.ws?.readyState}`);
        this.config.onError(error);
      };

      this.ws.onclose = (event) => {
        console.log(`🔌 WebSocket disconnected`, {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
          attempts: this.reconnectAttempts,
        });

        this.config.onClose(event);

        // Handle reconnection
        if (event.code === 1006 && this.reconnectAttempts < this.config.maxReconnectAttempts) {
          this.reconnectAttempts++;
          const delay = this.config.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
          console.log(`🔄 Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.config.maxReconnectAttempts})`);
          
          this.reconnectTimer = setTimeout(() => {
            this.connect();
          }, delay);
        } else {
          console.log('⚠️ WebSocket closed cleanly or max attempts reached. Using fallback.');
          // Fallback to polling or show error to user
        }
      };
    } catch (error) {
      console.error('❌ Failed to create WebSocket:', error);
      this.config.onError(error as Event);
    }
  }

  send(data: any): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      console.warn('⚠️ WebSocket not connected, cannot send message');
    }
  }

  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

// Usage
const wsClient = new SSOWebSocketClient({
  appSlug: 'app-3',
  endpoint: 'ws',
  onMessage: (data) => {
    console.log('Received message:', data);
    // Handle message
  },
  onError: (error) => {
    console.error('WebSocket error:', error);
    // Show error to user or fallback to polling
  },
  onClose: (event) => {
    if (event.code === 1006) {
      console.error('Connection closed abnormally - check server logs');
    }
  },
});

// Connect
wsClient.connect();

// Send message
wsClient.send({ type: 'ping' });

// Disconnect when done
// wsClient.disconnect();
```

### React Hook Example

```typescript
import { useEffect, useRef, useState, useCallback } from 'react';

interface UseWebSocketOptions {
  appSlug: string;
  endpoint?: string;
  enabled?: boolean;
  maxReconnectAttempts?: number;
}

export function useSSOWebSocket(options: UseWebSocketOptions) {
  const { appSlug, endpoint = 'ws', enabled = true, maxReconnectAttempts = 5 } = options;
  
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Event | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);

  const connect = useCallback(() => {
    if (!enabled) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/${appSlug}/${endpoint}`;

    // No token needed - browser automatically sends httpOnly cookies
    // Ensure user is authenticated and cookie is set before connecting
    console.log(`🔌 Connecting to WebSocket: ${wsUrl}`);

    try {
      // Browser automatically sends httpOnly cookies
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('✅ WebSocket connected');
        setConnected(true);
        setError(null);
        reconnectAttemptsRef.current = 0;
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          console.log('📨 Received:', data);
          // Handle message via callback or state
        } catch (err) {
          console.error('❌ Failed to parse message:', err);
        }
      };

      ws.onerror = (event) => {
        console.error('❌ WebSocket error:', event);
        setError(event);
        setConnected(false);
      };

      ws.onclose = (event) => {
        console.log(`🔌 WebSocket disconnected`, {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
        });
        setConnected(false);

        // Reconnect logic
        if (
          event.code === 1006 &&
          reconnectAttemptsRef.current < maxReconnectAttempts
        ) {
          reconnectAttemptsRef.current++;
          const delay = 2000 * Math.pow(2, reconnectAttemptsRef.current - 1);
          console.log(`🔄 Reconnecting in ${delay}ms`);
          
          reconnectTimerRef.current = setTimeout(() => {
            connect();
          }, delay);
        }
      };
    } catch (err) {
      console.error('❌ Failed to create WebSocket:', err);
      setError(err as Event);
    }
  }, [appSlug, endpoint, enabled, maxReconnectAttempts]);

  const send = useCallback((data: any) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    } else {
      console.warn('⚠️ WebSocket not connected');
    }
  }, []);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (enabled) {
      connect();
    }
    return () => {
      disconnect();
    };
  }, [enabled, connect, disconnect]);

  return { connected, error, send, disconnect, reconnect: connect };
}

// Usage in component
function MyComponent() {
  const { connected, error, send } = useSSOWebSocket({
    appSlug: 'app-3',
    endpoint: 'ws',
    enabled: true,
  });

  return (
    <div>
      <p>Status: {connected ? '✅ Connected' : '❌ Disconnected'}</p>
      {error && <p>Error: {error.type}</p>}
      <button onClick={() => send({ type: 'ping' })}>
        Send Ping
      </button>
    </div>
  );
}
```

---

## Backend Configuration Checklist

### Multi-App Setup

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_apps_db"
)

app = engine.create_multi_app(
    apps=[
        {
            "slug": "app-3",
            "manifest": Path("./apps/app-3/manifest.json"),
            "path_prefix": "/app-3",
        },
    ],
    title="My Platform",
)
```

### Manifest Configuration

```json
{
  "schema_version": "2.0",
  "slug": "app-3",
  "name": "My App",
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor"],
    "require_role": "viewer"
  },
  "websockets": {
    "realtime": {
      "path": "/ws",
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
      "https://yourdomain.com"
    ],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

---

## Server-Side Debugging

### Enable Debug Logging

```python
import logging

# Set up detailed logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("mdb_engine")

# You should see logs like:
# DEBUG: Set default CORS config on parent app
# DEBUG: ✅ Merged CORS config from child app 'app-3' to parent app
# INFO: CSRFMiddleware added to parent app for WebSocket origin validation
# INFO: 🔌 WebSocket connection attempt for app 'app-3'
```

### Check Parent App State

```python
# After creating multi-app, before starting server
async with app.router.lifespan_context(app):
    # Check CORS config
    print("CORS Config:", app.state.cors_config)
    # Should show:
    # {
    #   "enabled": True,
    #   "allow_origins": ["http://localhost:3000", ...],
    #   "allow_credentials": True,
    #   ...
    # }
    
    # Check WebSocket routes
    ws_routes = [r for r in app.routes if hasattr(r, 'path') and '/ws' in str(r.path)]
    print("WebSocket Routes:", [str(r.path) for r in ws_routes])
    # Should include: /app-3/ws
```

---

## Client-Side Debugging

### Browser Console Debugging

```javascript
// 1. Check current origin
console.log("Origin:", window.location.origin);

// 2. Check cookies
console.log("Cookies:", document.cookie);

// 3. Test WebSocket connection manually
const ws = new WebSocket('ws://localhost:8000/app-3/ws');
ws.onopen = () => console.log('✅ Connected');
ws.onerror = (e) => console.error('❌ Error:', e);
ws.onclose = (e) => console.log('🔌 Closed:', e.code, e.reason);

// 4. Check Network tab
// - Look for WebSocket upgrade request
// - Check Request Headers (should include Origin, Cookie)
// - Check Response Headers (should include CORS headers)
```

### Network Tab Inspection

1. **Open Browser DevTools → Network Tab**
2. **Filter by "WS" (WebSocket)**
3. **Click on the WebSocket connection**
4. **Check:**
   - **Request Headers:**
     - `Origin: http://localhost:3000` ✅
     - `Cookie: token=<jwt-token>; csrf_token=<csrf-token>` ✅
     - `Upgrade: websocket` ✅
     - `Connection: Upgrade` ✅
   
   - **Response Headers:**
     - `Access-Control-Allow-Origin: http://localhost:3000` ✅
     - `Access-Control-Allow-Credentials: true` ✅
   
   - **Status:**
     - `101 Switching Protocols` ✅ (success)
     - `403 Forbidden` ❌ (CORS/CSRF issue)
     - `426 Upgrade Required` ❌ (protocol issue)

---

## Quick Fixes

### Fix 1: Update CORS Config

```json
// In manifest.json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"],  // Your frontend origin
    "allow_credentials": true,  // REQUIRED
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

### Fix 2: Verify WebSocket URL and Cookie

```javascript
// ✅ CORRECT: Include path prefix, browser sends cookies automatically
const wsUrl = `ws://localhost:8000/app-3/ws`;
const ws = new WebSocket(wsUrl);

// ❌ WRONG (missing path prefix)
const wsUrl = `ws://localhost:8000/ws`;

// ❌ WRONG (using query params - deprecated)
const ws = new WebSocket(`${wsUrl}?token=${token}`);
```

### Fix 3: Verify Token is Valid

```javascript
// Check token before connecting
const token = getAuthToken();
if (!token) {
  console.error('No token - authenticate first');
  // Redirect to login or refresh token
  return;
}

// Check token expiration
const payload = JSON.parse(atob(token.split('.')[1]));
const expiresAt = payload.exp * 1000;
if (Date.now() >= expiresAt) {
  console.warn('Token expired - refreshing...');
  await refreshToken();
}

// Connect with valid token
const ws = new WebSocket(url, [getAuthToken()]);
```

### Fix 4: Restart Server After Config Changes

```bash
# After changing manifest.json, restart server
uvicorn main:app --reload
```

---

## Still Having Issues?

1. **Check server logs** for detailed error messages
2. **Verify all checklist items** above
3. **Test with curl** to isolate frontend issues:
   ```bash
   curl -i -N \
     -H "Connection: Upgrade" \
     -H "Upgrade: websocket" \
     -H "Origin: http://localhost:3000" \
     -H "Cookie: mdb_auth_token=YOUR_TOKEN" \
     http://localhost:8000/app-3/ws
   ```
4. **Enable debug logging** on both client and server
5. **Check browser console** for CORS errors or network issues

---

**Related Documentation:**
- [WebSocket Security Guide](./WEBSOCKET_SECURITY_MULTI_APP_SSO.md) - **Comprehensive security guide**
- [WebSocket + SSO Multi-App Guide](./WEBSOCKET_SSO_MULTI_APP.md)
- [SSO Multi-App Setup Guide](./SSO_MULTI_APP_SETUP.md)
- [SSO Multi-App Cheat Sheet](../api/SSO_MULTI_APP_CHEATSHEET.md)
