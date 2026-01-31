# WebSocket Security: Elegant Multi-App Solution

## Overview

MDB-Engine implements an **elegant WebSocket security model** for multi-app setups that balances security with simplicity. This document explains the design decisions and how to use it.

## The Problem

In multi-app SSO setups, WebSocket connections need:
1. ✅ Authentication (JWT token in httpOnly cookie)
2. ✅ CSRF protection
3. ✅ Origin validation
4. ✅ Multi-app support (parent app manages security)

Traditional approaches require CSRF cookies for every WebSocket connection, which adds complexity and can cause issues in multi-app setups.

## The Elegant Solution

**Default**: Origin validation + SameSite cookies provide **sufficient CSRF protection** for WebSocket connections. CSRF cookie is **optional** and can be enabled per-endpoint for extra strict security.

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
// Make GET request first to receive CSRF cookie
await fetch('/api/endpoint', { credentials: 'include' });

// Then connect WebSocket (CSRF cookie sent automatically)
const ws = new WebSocket('ws://localhost:8000/app1/ws');
```

## Best Practices

1. **Use Default (csrf_required: false)**
   - Simpler and still secure
   - Origin + SameSite provide sufficient protection
   - Recommended for most use cases

2. **Enable CSRF Cookie Only When Needed**
   - Extra strict compliance requirements
   - Defense-in-depth requirements
   - Legacy system integration

3. **Ensure Proper CORS Configuration**
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

4. **Verify SameSite Cookie Settings**
   - Cookies should be set with `SameSite=Lax` or `SameSite=Strict`
   - MDB-Engine sets this automatically for auth cookies

## References

- [WebSocket Security Guide](./WEBSOCKET_SECURITY_MULTI_APP_SSO.md) - Comprehensive security documentation
- [WebSocket + SSO Multi-App Guide](./WEBSOCKET_SSO_MULTI_APP.md) - Multi-app setup guide
- [Manifest Reference](../MANIFEST_REFERENCE.md) - Complete manifest.json reference
