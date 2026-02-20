# CSRF Protection Guide

## Overview

MDB-Engine provides comprehensive **Cross-Site Request Forgery (CSRF) protection** for both HTTP requests and WebSocket connections. This guide explains how CSRF protection works, how to configure it, and best practices.

## What is CSRF?

Cross-Site Request Forgery (CSRF) is an attack where a malicious website tricks a user's browser into making unauthorized requests to a different website where the user is authenticated.

**Example Attack:**
1. User is logged into `bank.com`
2. User visits malicious site `evil.com`
3. `evil.com` includes: `<img src="https://bank.com/transfer?to=attacker&amount=1000">`
4. Browser automatically sends request with user's cookies
5. Bank processes transfer (user didn't intend this!)

**CSRF Protection prevents this** by requiring additional validation beyond cookies.

## CSRF Protection for HTTP Requests

### How It Works

MDB-Engine uses the **double-submit cookie pattern** for HTTP CSRF protection:

1. **Token Generation**: CSRF token generated and set as cookie (`csrf_token`)
2. **Token Submission**: State-changing requests (POST, PUT, DELETE, PATCH) must include token in `X-CSRF-Token` header
3. **Token Validation**: Server validates header matches cookie value (constant-time comparison)

### Configuration

**Basic Configuration:**
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
    "mode": "shared",
    "csrf_protection": {
      "enabled": true,
      "exempt_routes": ["/api/webhooks/*", "/health", "/api/public/*"],
      "token_ttl": 3600,
      "rotate_tokens": false
    }
  }
}
```

**Configuration Options:**
- `enabled`: Enable/disable CSRF protection (default: `true` for shared auth)
- `exempt_routes`: Routes exempt from CSRF validation (supports wildcards: `/api/*`)
- `token_ttl`: Token time-to-live in seconds (default: 3600 = 1 hour)
- `rotate_tokens`: Rotate token on each request (default: `false`)

### Frontend Integration

#### Reading CSRF Token

```javascript
// Helper function to read cookies
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

// Get CSRF token
const csrfToken = getCookie('csrf_token');
```

#### Including in Requests

**POST Request:**
```javascript
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
```

**PUT Request:**
```javascript
async function updateItem(id, data) {
    const response = await fetch(`/api/items/${id}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': getCookie('csrf_token')
        },
        credentials: 'same-origin',
        body: JSON.stringify(data)
    });
    return response.json();
}
```

**DELETE Request:**
```javascript
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

#### Using Fetch Interceptor (Recommended)

```javascript
// Create fetch wrapper that automatically includes CSRF token
const csrfFetch = async (url, options = {}) => {
    const csrfToken = getCookie('csrf_token');
    
    const headers = {
        ...options.headers,
        'X-CSRF-Token': csrfToken
    };
    
    return fetch(url, {
        ...options,
        headers,
        credentials: 'same-origin'
    });
};

// Use it everywhere
await csrfFetch('/api/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'New Item' })
});
```

### Exempt Routes

Routes exempt from CSRF validation (safe methods and public endpoints):

**Automatic Exemptions:**
- GET, HEAD, OPTIONS, TRACE (safe methods)
- Routes matching `exempt_routes` patterns

**Example:**
```json
{
  "auth": {
    "csrf_protection": {
      "exempt_routes": [
        "/api/webhooks/*",  // Webhook endpoints (external callbacks)
        "/health",           // Health checks
        "/api/public/*"      // Public API endpoints
      ]
    }
  }
}
```

### Error Handling

**403 Forbidden Errors:**

1. **Missing CSRF Token:**
   ```json
   {
     "detail": "CSRF token missing"
   }
   ```
   Solution: Include `X-CSRF-Token` header

2. **Invalid CSRF Token:**
   ```json
   {
     "detail": "CSRF token invalid"
   }
   ```
   Solution: Token mismatch - make sure header matches cookie

3. **Expired CSRF Token:**
   ```json
   {
     "detail": "CSRF token expired or invalid"
   }
   ```
   Solution: Make a GET request to refresh token, then retry

**Error Handling Example:**
```javascript
async function submitWithRetry(data) {
    try {
        return await csrfFetch('/api/items', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
    } catch (error) {
        if (error.status === 403) {
            // Token might be expired - refresh and retry
            await fetch('/api/items'); // GET request refreshes token
            return await csrfFetch('/api/items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
        }
        throw error;
    }
}
```

## CSRF Protection for WebSockets

### Overview

WebSocket CSRF protection uses a **layered approach**:

1. **Origin Validation** (Primary Defense - REQUIRED)
   - Validates `Origin` header against CORS config
   - Prevents cross-origin WebSocket hijacking (CSWSH)
   - **This is the primary CSRF protection mechanism**

2. **SameSite Cookies** (Secondary Defense - REQUIRED)
   - Cookies set with `SameSite=Lax` or `SameSite=Strict`
   - Browser prevents cross-site cookie sending
   - Additional CSRF protection layer

3. **Ticket Authentication** (Authentication Layer)
   - Short-lived (10 seconds), single-use tickets
   - Prevents replay attacks
   - Works seamlessly with CSRF protection

4. **Optional CSRF Cookie** (Extra Strict Mode)
   - Can be enabled via `auth.csrf_required: true`
   - Double-submit cookie pattern
   - Only needed for extra strict compliance requirements

### Configuration

**Default (Recommended):**
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
    "allow_origins": ["https://yourdomain.com"],
    "allow_credentials": true
  }
}
```

**Extra Strict Mode:**
```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": true  // Requires CSRF cookie in addition to Origin validation
      }
    }
  }
}
```

### Why Origin + SameSite is Sufficient

1. **Origin Header Cannot Be Spoofed**
   - Browsers enforce Origin header on WebSocket upgrades
   - Attacker cannot forge Origin header from malicious site
   - Server validates against whitelist

2. **SameSite Cookies Prevent Cross-Site Requests**
   - Browser enforces SameSite attribute
   - Cookies not sent on cross-site requests
   - Additional protection layer

3. **Combined Protection**
   - Origin validation = primary defense
   - SameSite cookies = secondary defense
   - Together, they provide sufficient CSRF protection

### Ticket-Based Authentication

**Flow:**
```
1. User logs in → JWT stored in httpOnly cookie
2. Client requests ticket → POST /auth/ticket (sends JWT cookie)
3. Server validates JWT → Generates one-time ticket (UUID, 10-second TTL)
4. Client connects WebSocket → ws://host/app/ws?ticket=<uuid>
5. Server validates ticket → Consumes ticket (single-use)
6. WebSocket connection established
```

**Client Implementation:**
```javascript
// Step 1: Get ticket (must be done right before connecting)
const ticketRes = await fetch('/auth/ticket', {
    method: 'POST',
    credentials: 'include'  // Sends JWT cookie
});
const { ticket } = await ticketRes.json();

// Step 2: Connect WebSocket with ticket (within 10 seconds)
const ws = new WebSocket(`wss://api.example.com/app1/ws?ticket=${ticket}`);

ws.onopen = () => {
    console.log('WebSocket connected securely');
};
```

**Benefits:**
- ✅ Short-lived (10 seconds) reduces interception window
- ✅ Single-use prevents replay attacks
- ✅ No database lookups (faster)
- ✅ Works seamlessly with CSRF protection

### When to Use CSRF Cookie for WebSockets

CSRF cookie (`csrf_required: true`) adds an extra validation layer but is **redundant** when Origin + SameSite are properly configured. Use it for:

- Extra strict compliance requirements (PCI-DSS, HIPAA)
- Defense-in-depth (multiple validation layers)
- Legacy system integration requirements

**Client Implementation (CSRF Cookie Required):**
```javascript
// Step 1: Make GET request to receive CSRF cookie
await fetch('/api/endpoint', { credentials: 'include' });

// Step 2: Get ticket for WebSocket connection
const ticketRes = await fetch('/auth/ticket', {
  method: 'POST',
  credentials: 'include'
});
const { ticket } = await ticketRes.json();

// Step 3: Connect WebSocket with ticket
const ws = new WebSocket(`ws://localhost:8000/app1/ws?ticket=${ticket}`);
```

## Security Best Practices

### 1. Always Use HTTPS in Production

CSRF tokens are only secure over HTTPS. In production:
- Use HTTPS for all requests
- Set `secure` flag on cookies
- Use `SameSite=Strict` for maximum security

### 2. Configure CORS Properly

**Development:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000", "http://localhost:8000"],
    "allow_credentials": true
  }
}
```

**Production:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["https://yourdomain.com"],
    "allow_credentials": true
  }
}
```

**Never use wildcards (`*`) in production!**

### 3. Use SameSite Cookies

MDB-Engine automatically sets `SameSite=Lax` for auth cookies. This provides additional CSRF protection.

### 4. Exempt Only Safe Routes

Only exempt routes that are:
- Public (no authentication required)
- Safe (no state changes)
- External callbacks (webhooks)

**Bad Example:**
```json
{
  "csrf_protection": {
    "exempt_routes": ["/api/*"]  // ❌ Too broad - exposes all APIs
  }
}
```

**Good Example:**
```json
{
  "csrf_protection": {
    "exempt_routes": ["/api/webhooks/*", "/health"]  // ✅ Specific exemptions
  }
}
```

### 5. Handle Token Expiration Gracefully

```javascript
// Retry logic for expired tokens
async function fetchWithRetry(url, options) {
    try {
        return await csrfFetch(url, options);
    } catch (error) {
        if (error.status === 403) {
            // Token expired - refresh and retry
            await fetch('/api/refresh'); // GET request refreshes token
            return await csrfFetch(url, options);
        }
        throw error;
    }
}
```

### 6. Use Environment-Specific Secrets

Set `MDB_ENGINE_CSRF_SECRET` environment variable for HMAC token signing:

```bash
# Development
export MDB_ENGINE_CSRF_SECRET="dev_secret_change_in_production"

# Production
export MDB_ENGINE_CSRF_SECRET="$(openssl rand -hex 32)"
```

## Troubleshooting

### CSRF Token Missing

**Symptom:** `403 Forbidden` with `"CSRF token missing"`

**Solutions:**
1. Make a GET request first to receive CSRF cookie
2. Include `X-CSRF-Token` header in request
3. Ensure `credentials: 'same-origin'` is set

### CSRF Token Invalid

**Symptom:** `403 Forbidden` with `"CSRF token invalid"`

**Solutions:**
1. Check that header token matches cookie token
2. Ensure token hasn't expired (default: 1 hour)
3. Make a GET request to refresh token

### WebSocket Origin Validation Failed

**Symptom:** `403 Forbidden` with `"Invalid origin for WebSocket connection"`

**Solutions:**
1. Check CORS configuration in manifest.json
2. Ensure `allow_origins` includes your frontend origin
3. Verify `Origin` header is being sent by browser

### WebSocket Ticket Expired

**Symptom:** `403 Forbidden` with `"WebSocket ticket expired or invalid"`

**Solutions:**
1. Tickets expire in 10 seconds - get a new one
2. Connect WebSocket immediately after getting ticket
3. Implement retry logic with fresh ticket

## References

- [WebSocket Security Guide](./WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md) - WebSocket security details
- [Security Documentation](../SECURITY.md) - General security documentation
- [Manifest Reference](../MANIFEST_REFERENCE.md) - Complete manifest.json reference
- [WebSocket Tickets Example](../../examples/advanced/websocket-tickets/) - Complete working example
