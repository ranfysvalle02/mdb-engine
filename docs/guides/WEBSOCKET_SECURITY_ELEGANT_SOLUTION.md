# WebSocket Security: Elegant Solution

## Overview

MDB-Engine implements an **elegant WebSocket security model** that balances security with simplicity. This document explains the design decisions and how to use it for both single-app and multi-app setups.

## The Problem

WebSocket connections need:
1. ✅ Authentication (JWT token in httpOnly cookie)
2. ✅ CSRF protection
3. ✅ Origin validation
4. ✅ Consistent security model (single-app and multi-app)

Traditional approaches require CSRF cookies for every WebSocket connection, which adds complexity and can cause issues in multi-app setups.

## Ticket-Based Authentication (Preferred)

MDB-Engine now supports **ticket-based authentication** for WebSocket connections, which is the **preferred method** for both single-app and multi-app SSO setups:

- **Short-lived tickets**: 10-second TTL reduces interception window
- **Single-use**: Tickets consumed immediately after validation (prevents replay attacks)
- **In-memory storage**: No database lookups, faster validation
- **Simpler flow**: JWT → Ticket → WebSocket connection
- **No dependencies**: Works without encryption service (unlike session keys)
- **Secure-by-default**: Works seamlessly with CSRF protection

### Ticket Exchange Flow

```
1. User logs in → JWT stored in httpOnly cookie
2. Client requests ticket → POST /auth/ticket (sends JWT cookie)
3. Server validates JWT → Generates one-time ticket (UUID)
4. Client connects WebSocket → ws://host/app/ws?ticket=<uuid>
5. Server validates ticket → Consumes ticket (single-use)
6. WebSocket connection established
```

### Ticket Implementation Details

**Ticket Store:**
- Tickets are stored in-memory using `WebSocketTicketStore`
- Each ticket is a UUID (v4) with metadata:
  - `user_id`: Authenticated user ID
  - `user_email`: User email (optional)
  - `app_slug`: App slug for scoping (optional)
  - `exp`: Expiration timestamp (default: 10 seconds)
  - `created_at`: Creation timestamp

**Ticket Endpoint:**
- Endpoint: `POST /auth/ticket`
- Requires: Authenticated user (JWT cookie)
- Returns: `{"ticket": "<uuid>", "expires_in": 10}`
- Auto-registered when using `create_app()` or `create_multi_app()`

**Ticket Validation:**
- Tickets are validated atomically (check + consume in single operation)
- Expired tickets are automatically cleaned up
- Invalid or already-used tickets return 403 Forbidden
- Ticket validation happens in CSRF middleware before WebSocket handler

**Security Properties:**
- ✅ **Short TTL**: 10-second expiration reduces attack window
- ✅ **Single-use**: Atomic consume prevents replay attacks
- ✅ **No persistence**: In-memory storage means no database exposure
- ✅ **Fast validation**: O(1) lookup, no encryption overhead

## The Elegant Solution

**Default**: Origin validation + SameSite cookies provide **sufficient CSRF protection** for WebSocket connections. CSRF cookie is **optional** and can be enabled per-endpoint for extra strict security.

### FastAPI Integration (v0.7.0+)

MDB-Engine now uses **FastAPI's native `APIRouter` approach** for WebSocket registration in both single-app and multi-app modes. This provides:

- ✅ **Full FastAPI Feature Support**: Dependency injection, OpenAPI documentation, request/response models
- ✅ **Consistency**: Same registration pattern across single-app and multi-app modes
- ✅ **Best Practices**: Follows FastAPI's recommended WebSocket registration patterns
- ✅ **Maintainability**: Uses FastAPI abstractions instead of low-level Starlette APIs
- ✅ **Route Priority**: WebSocket routes are registered before mounted apps to ensure proper routing

**Technical Details:**
- Single-app mode: Uses `APIRouter().websocket(path)(handler)` → `app.include_router(ws_router)`
- Multi-app mode: Uses `APIRouter().websocket(full_path)(handler)` → `parent_app.include_router(ws_router)` (before mounting child apps)
- Routes are created as `APIWebSocketRoute` instances (FastAPI's WebSocket route type)

### Why This Works

1. **Origin Validation** (Primary CSRF Defense)
   - WebSocket upgrade requests include `Origin` header
   - Server validates `Origin` against `allow_origins` from CORS config
   - Prevents cross-origin WebSocket connections
   - **This is the primary CSRF protection mechanism**

2. **SameSite Cookies** (Secondary CSRF Defense)
   - Cookies set with `SameSite=Lax` or `SameSite=Strict`
   - Browser prevents cross-site cookie sending
   - Additional layer of CSRF protection

3. **Optional CSRF Cookie** (Extra Strict Mode)
   - Can be enabled per-endpoint via `auth.csrf_required: true`
   - Provides double-submit cookie pattern validation
   - Useful for extra strict security requirements

### Security Model

```
┌─────────────────────────────────────────────────────────┐
│         WebSocket Security Layers                        │
├─────────────────────────────────────────────────────────┤
│ Layer 1: Origin Validation (REQUIRED)                   │
│   • Validates Origin header against CORS config        │
│   • Prevents cross-origin connections                    │
│   • Primary CSRF defense                                │
├─────────────────────────────────────────────────────────┤
│ Layer 2: SameSite Cookies (REQUIRED)                    │
│   • Cookies set with SameSite attribute                 │
│   • Browser prevents cross-site cookie sending          │
│   • Secondary CSRF defense                              │
├─────────────────────────────────────────────────────────┤
│ Layer 3: CSRF Cookie (OPTIONAL)                         │
│   • Enabled via auth.csrf_required: true                │
│   • Double-submit cookie pattern                        │
│   • Extra strict security requirement                   │
└─────────────────────────────────────────────────────────┘
```

## Configuration

### Default (Recommended)

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": false  // Default: elegant and secure
      }
    }
  }
}
```

**Benefits:**
- ✅ Simpler client implementation (no CSRF cookie needed)
- ✅ Still secure (Origin + SameSite provide CSRF protection)
- ✅ Works seamlessly in multi-app setups
- ✅ Parent app manages security validation

### Extra Strict Mode

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": true  // Extra strict: requires CSRF cookie
      }
    }
  }
}
```

**When to Use:**
- Extra strict security requirements
- Compliance requirements that mandate CSRF cookies
- Additional defense-in-depth layer

## How It Works

### Parent App Security Management

1. **WebSocket Config Storage**
   - WebSocket configs stored in `parent_app.state.websocket_configs`
   - Keyed by app slug: `{app_slug: websockets_config}`
   - CSRF middleware accesses this to check `csrf_required` setting

2. **CSRF Middleware Flow**
   ```
   WebSocket Upgrade Request
   ↓
   CSRF Middleware intercepts
   ↓
   Validates Origin (REQUIRED)
   ↓
   Checks auth.csrf_required from manifest
   ↓
   If csrf_required=false: Allow (Origin + SameSite sufficient)
   If csrf_required=true: Validate CSRF cookie
   ↓
   Allow connection or reject with 403
   ```

3. **Path Matching**
   - CSRF middleware matches WebSocket path to app config
   - Uses full path (e.g., `/app-3/ws`) to find matching endpoint
   - Checks `auth.csrf_required` setting per-endpoint

## Security Analysis

### Why Origin + SameSite is Sufficient

1. **Origin Validation**
   - WebSocket protocol requires `Origin` header
   - Server validates against whitelist
   - **Prevents CSRF attacks** (attacker can't spoof Origin)

2. **SameSite Cookies**
   - Browser enforces SameSite attribute
   - Prevents cross-site cookie sending
   - **Additional CSRF protection layer**

3. **Combined Protection**
   - Origin validation = primary defense
   - SameSite cookies = secondary defense
   - **Together, they provide sufficient CSRF protection**

### When CSRF Cookie Adds Value

CSRF cookie provides additional validation but is **redundant** when Origin + SameSite are properly configured. It's useful for:
- Extra strict compliance requirements
- Defense-in-depth (multiple validation layers)
- Legacy systems that require CSRF tokens

## Multi-App Benefits

1. **Parent App Manages Security**
   - Single point of security validation
   - Consistent security across all child apps
   - Easier to audit and maintain

2. **Per-Endpoint Configuration**
   - Each WebSocket endpoint can configure CSRF requirement
   - Flexible security model
   - Apps can choose strictness level

3. **Simplified Client Code**
   - No CSRF cookie management by default
   - Browser handles cookie transmission automatically
   - Cleaner, simpler client implementation

## Migration Guide

### From CSRF Cookie Required to Optional

**Before:**
```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true
      }
    }
  }
}
```
*(CSRF cookie was implicitly required)*

**After:**
```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": false  // Explicit: use elegant default
      }
    }
  }
}
```

**Client Code:**
- ✅ No changes needed (CSRF cookie not required)
- ✅ Simpler implementation
- ✅ Still secure (Origin + SameSite)

### Keeping CSRF Cookie Required

If you need extra strict security:

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "auth": {
        "required": true,
        "csrf_required": true  // Extra strict mode
      }
    }
  }
}
```

**Client Code:**
```javascript
// Step 1: Get ticket for WebSocket connection
const ticketRes = await fetch('/auth/ticket', {
  method: 'POST',
  credentials: 'include' // Sends JWT cookie
});
const { ticket } = await ticketRes.json();

// Step 2: Connect WebSocket with ticket
const ws = new WebSocket(`ws://localhost:8000/app1/ws?ticket=${ticket}`);
```

## Authentication Method

MDB-Engine uses **ticket-based authentication** for WebSocket connections.

**Tickets are short-lived (10 seconds), single-use, and stored in-memory.**

### Complete Client Implementation Example

```javascript
// Step 1: Login (existing flow - JWT stored in httpOnly cookie)
const loginRes = await fetch('/auth-hub/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  credentials: 'include',
  body: JSON.stringify({
    email: 'user@example.com',
    password: 'password123'
  })
});

if (!loginRes.ok) {
  throw new Error('Login failed');
}

// Step 2: Exchange JWT for ticket (must be done right before WebSocket connection)
const ticketRes = await fetch('/auth/ticket', {
  method: 'POST',
  credentials: 'include'  // Sends JWT cookie automatically
});

if (!ticketRes.ok) {
  throw new Error('Failed to get ticket');
}

const { ticket, expires_in } = await ticketRes.json();
console.log(`Got ticket, expires in ${expires_in} seconds`);

// Step 3: Connect WebSocket with ticket (must be done within 10 seconds)
const ws = new WebSocket(`wss://api.example.com/app1/ws?ticket=${ticket}`);

ws.onopen = () => {
  console.log('WebSocket connected securely');
  // Ticket has been consumed - connection is authenticated
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
  // If ticket expired, get a new one and reconnect
};

ws.onclose = (event) => {
  console.log('WebSocket closed:', event.code, event.reason);
};
```

### Error Handling

**Common Errors:**

1. **401 Unauthorized** (`/auth/ticket` endpoint):
   - User not logged in (no JWT cookie)
   - Solution: Login first

2. **403 Forbidden** (WebSocket connection):
   - Ticket expired (older than 10 seconds)
   - Ticket already used (single-use)
   - Invalid ticket format
   - Solution: Get a new ticket and reconnect

3. **403 Forbidden** (Origin validation):
   - Origin header doesn't match CORS config
   - Solution: Ensure CORS is configured correctly

**Best Practice:**
```javascript
// Always get ticket right before connecting
async function connectWebSocket() {
  try {
    const ticketRes = await fetch('/auth/ticket', {
      method: 'POST',
      credentials: 'include'
    });
    
    if (!ticketRes.ok) {
      throw new Error('Failed to get ticket');
    }
    
    const { ticket } = await ticketRes.json();
    
    // Connect immediately (ticket expires in 10 seconds)
    const ws = new WebSocket(`ws://localhost:8000/app1/ws?ticket=${ticket}`);
    
    return ws;
  } catch (error) {
    console.error('WebSocket connection failed:', error);
    throw error;
  }
}
```

### Benefits

- ✅ **No database lookups** (faster validation)
- ✅ **No encryption service required** (simpler setup)
- ✅ **Single-use prevents replay attacks** (atomic consume)
- ✅ **Short TTL reduces interception window** (10 seconds)
- ✅ **Simpler implementation** (no session key management)
- ✅ **Secure-by-default for multi-app SSO** (works with shared auth)
- ✅ **CSRF protection built-in** (Origin validation + SameSite cookies)

## Best Practices

1. **Use Ticket Authentication (Preferred)**
   - Simpler and faster (no database lookups)
   - Single-use prevents replay attacks
   - Recommended for multi-app SSO setups
   - No encryption service required

2. **Use Default (csrf_required: false)**
   - Simpler and still secure
   - Origin + SameSite provide sufficient protection
   - Recommended for most use cases

3. **Enable CSRF Cookie Only When Needed**
   - Extra strict compliance requirements
   - Defense-in-depth requirements
   - Legacy system integration

4. **Ensure Proper CORS Configuration**
   ```json
   {
     "cors": {
       "enabled": true,
       "allow_origins": ["https://yourdomain.com"],
       "allow_credentials": true
     }
   }
   ```
   - Required for Origin validation to work
   - Use specific origins in production (not wildcards)

5. **Verify SameSite Cookie Settings**
   - Cookies should be set with `SameSite=Lax` or `SameSite=Strict`
   - MDB-Engine sets this automatically for auth cookies

## CSRF Protection for WebSockets

### How CSRF Protection Works

MDB-Engine provides **layered CSRF protection** for WebSocket connections:

1. **Origin Validation** (Primary Defense - REQUIRED)
   - WebSocket upgrade requests include `Origin` header
   - Server validates against `cors.allow_origins` from manifest
   - Prevents cross-origin WebSocket hijacking (CSWSH)
   - **This is the primary CSRF protection mechanism**

2. **SameSite Cookies** (Secondary Defense - REQUIRED)
   - JWT cookies set with `SameSite=Lax` or `SameSite=Strict`
   - Browser prevents cross-site cookie sending
   - Additional layer of CSRF protection

3. **Ticket Authentication** (Authentication Layer)
   - Tickets are single-use and short-lived
   - Prevents replay attacks
   - Works seamlessly with CSRF protection

4. **Optional CSRF Cookie** (Extra Strict Mode)
   - Can be enabled via `auth.csrf_required: true`
   - Double-submit cookie pattern
   - Only needed for extra strict compliance requirements

### CSRF Configuration

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

### CSRF Middleware Flow

```
WebSocket Upgrade Request
  ↓
CSRF Middleware intercepts
  ↓
Validates Origin header (REQUIRED)
  ↓
If Origin invalid → Reject with 403
  ↓
If Origin valid:
  ↓
  Check auth.csrf_required from manifest
  ↓
  If csrf_required=false:
    → Allow (Origin + SameSite sufficient)
  If csrf_required=true:
    → Validate ticket or CSRF cookie
    → If valid → Allow
    → If invalid → Reject with 403
  ↓
WebSocket handler receives request
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

### When to Use CSRF Cookie

CSRF cookie (`csrf_required: true`) adds an extra validation layer but is **redundant** when Origin + SameSite are properly configured. Use it for:

- Extra strict compliance requirements (PCI-DSS, HIPAA)
- Defense-in-depth (multiple validation layers)
- Legacy system integration requirements

## References

- [WebSocket Tickets Example](../../examples/advanced/websocket-tickets/) - Complete working example
- [Multi-App Guide](./MULTI_APP_GUIDE.md) - Multi-app setup guide
- [SSO Multi-App Setup](./SSO_MULTI_APP_SETUP.md) - SSO configuration
- [Manifest Reference](../MANIFEST_REFERENCE.md) - Complete manifest.json reference
- [CSRF Protection](../SECURITY.md#csrf-protection) - General CSRF documentation
