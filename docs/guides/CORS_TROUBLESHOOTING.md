# CORS Troubleshooting Guide

Complete guide to diagnosing and fixing CORS (Cross-Origin Resource Sharing) issues in MDB Engine applications.

## Quick Reference: Common CORS Errors

| Error | Browser Console | Server Log | Cause | Quick Fix |
|-------|----------------|------------|-------|-----------|
| CORS policy blocked | `Access-Control-Allow-Origin` missing | Origin not in allowed list | Origin not configured | Add origin to `cors.allow_origins` |
| Credentials not sent | `credentials: 'include'` ignored | - | Wildcard with credentials | Use specific origins |
| Preflight fails | `OPTIONS` returns 403 | Origin validation failed | CORS not enabled or wrong origin | Enable CORS, check origin |
| WebSocket origin rejected | Connection fails immediately | `Invalid origin` | Origin mismatch | Add WebSocket origin to `allow_origins` |

## Table of Contents

- [Common CORS Errors](#common-cors-errors)
- [Wildcard + Credentials Issue](#wildcard--credentials-issue)
- [Origin Mismatch Issues](#origin-mismatch-issues)
- [WebSocket CORS Issues](#websocket-cors-issues)
- [Multi-App CORS Configuration](#multi-app-cors-configuration)
- [Debugging Checklist](#debugging-checklist)
- [Production Best Practices](#production-best-practices)

---

## Common CORS Errors

### Error 1: "Access-Control-Allow-Origin" Header Missing

**Browser Console:**
```
Access to fetch at 'http://localhost:8000/api/data' from origin 'http://localhost:3000' 
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present 
on the requested resource.
```

**Server Log:**
```
WebSocket origin validation failed for 'my-app': origin=http://localhost:3000 
not in allowed_origins=['http://localhost:8000']
```

**Cause:** The requesting origin is not in the allowed origins list.

**Solution:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true
  }
}
```

### Error 2: Credentials Not Sent with Wildcard Origin

**Browser Console:**
```
The value of the 'Access-Control-Allow-Credentials' header in the response is 'true' 
which must be 'false' when the request's credentials mode is 'include' and 
'Access-Control-Allow-Origin' is '*'.
```

**Server Log:**
```
CORS config error for 'my-app': Cannot use wildcard origins (*) with 
allow_credentials=true. Browsers reject this combination for security reasons.
```

**Cause:** Browsers reject the combination of `allow_origins: ["*"]` with `allow_credentials: true`.

**Solution:** Use specific origins instead of wildcard:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true
  }
}
```

### Error 3: Preflight OPTIONS Request Fails

**Browser Console:**
```
Access to fetch at 'http://localhost:8000/api/data' from origin 'http://localhost:3000' 
has been blocked by CORS policy: Response to preflight request doesn't pass 
access control check: It does not have HTTP ok status.
```

**Server Log:**
```
OPTIONS /api/data 403
```

**Cause:** CORS is not enabled, or the origin is not allowed.

**Solution:**
1. Ensure CORS is enabled:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"]
  }
}
```

2. Check that the origin matches exactly (including protocol and port).

---

## Wildcard + Credentials Issue

### The Problem

Browsers enforce a security restriction: when `Access-Control-Allow-Credentials: true` is set, the `Access-Control-Allow-Origin` header **cannot** be `*`. This is a fundamental browser security feature to prevent credential leakage.

### Why This Happens

When using cookie-based authentication or sending credentials in requests, you need `allow_credentials: true`. However, if you also set `allow_origins: ["*"]`, browsers will reject the response.

### The Solution

**❌ Incorrect:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": true
  }
}
```

**✅ Correct:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true
  }
}
```

### Validation

MDB Engine validates this configuration and will raise an error during app initialization if you attempt to use wildcard with credentials:

```
ValueError: CORS config error for 'my-app': Cannot use wildcard origins (*) 
with allow_credentials=true. Browsers reject this combination for security reasons. 
Use specific origins instead, e.g., ['http://localhost:3000', 'https://example.com']
```

---

## Origin Mismatch Issues

### Common Causes

1. **Protocol Mismatch:** `http://` vs `https://`
2. **Port Mismatch:** `localhost:3000` vs `localhost:8000`
3. **Hostname Mismatch:** `localhost` vs `127.0.0.1`
4. **Trailing Slash:** `http://localhost:3000` vs `http://localhost:3000/`

### Origin Normalization

MDB Engine normalizes origins for comparison:

- **Protocol:** `ws://` and `wss://` are converted to `http://` and `https://`
- **Localhost variants:** `127.0.0.1`, `0.0.0.0`, `::1` are normalized to `localhost`
- **Default ports:** `:80` and `:443` are removed
- **Docker IPs:** In development, Docker container IPs (172.17.x.x, 172.20.x.x) are normalized to `localhost`

### Development Port Flexibility

In development mode, MDB Engine is more lenient with localhost port mismatches. If your server runs on port 8000 but your frontend connects from port 3000, both will be allowed if they're both localhost.

**Example:**
- Server: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- **Result:** Allowed in development (both are localhost)

### Production Strictness

In production, origins must match exactly. Always configure specific origins:

```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "https://app.yourdomain.com",
      "https://admin.yourdomain.com"
    ],
    "allow_credentials": true
  }
}
```

---

## WebSocket CORS Issues

### WebSocket Origin Validation

WebSocket connections require origin validation because:
1. Middleware may not intercept WebSocket upgrade requests
2. WebSocket handlers validate origins directly
3. CSWSH (Cross-Site WebSocket Hijacking) protection

### Common WebSocket CORS Errors

#### Error: "Invalid origin" on WebSocket Connection

**Browser Console:**
```
WebSocket connection to 'ws://localhost:8000/app1/ws' failed
```

**Server Log:**
```
WebSocket origin validation failed for 'app1': origin=http://localhost:3000 
not in allowed_origins=['http://localhost:8000']. 
Add 'http://localhost:3000' to manifest.json cors.allow_origins
```

**Solution:**
Add the frontend origin to CORS config:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": [
      "http://localhost:3000"
    ],
    "allow_credentials": true
  }
}
```

#### Error: WebSocket Connects Then Immediately Closes

**Server Log:**
```
WebSocket origin validation failed for 'app1': No CORS config found and 
not in development mode. Please configure CORS in manifest.json for production use.
```

**Solution:**
Enable CORS in your manifest:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"]
  }
}
```

**Note:** In production, use specific origins, not wildcard.

### WebSocket Origin Validation Flow

1. **Extract Origin:** From WebSocket upgrade request headers
2. **Get CORS Config:** From parent app (in multi-app setups) or child app
3. **Check Wildcard:** If `*` in allowed origins, allow immediately
4. **Normalize & Compare:** Normalize both origin and allowed origins, then compare
5. **Development Fallback:** If no config and in development, generate from server address
6. **Production Reject:** If no config and in production, reject connection

---

## Multi-App CORS Configuration

### How CORS Configs Merge

In multi-app setups, CORS configs are merged from child apps to the parent app:

1. **Parent App Default:** Parent app starts with `allow_origins: ["*"]` and `enabled: true`
2. **Child App Config:** Each child app's CORS config is merged into parent
3. **Wildcard Handling:** If any app has `["*"]`, merged config gets `["*"]`
4. **Credentials Merge:** If ANY child requires credentials, parent allows credentials
5. **Enabled Merge:** If ANY app has `enabled: true`, merged config is enabled

### Example: Multi-App CORS Merge

**Parent App (default):**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["*"],
    "allow_credentials": false
  }
}
```

**Child App 1:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"],
    "allow_credentials": true
  }
}
```

**Child App 2:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["https://admin.example.com"],
    "allow_credentials": false
  }
}
```

**Merged Result:**
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000", "https://admin.example.com"],
    "allow_credentials": true
  }
}
```

**Note:** If Child App 1 had `allow_origins: ["*"]`, the merged result would be `["*"]`.

### Validation During Merge

The merge process validates configurations:
- **Wildcard + Credentials:** Raises `ValueError` if detected
- **Invalid Types:** Ensures `allow_origins` is a list
- **Enabled Flag:** Properly merges using OR logic (not overwrite)

---

## Debugging Checklist

### Step 1: Verify CORS Configuration

Check your `manifest.json`:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"],
    "allow_credentials": true,
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

### Step 2: Check Server Logs

Look for CORS-related log messages:
```
✅ Merged CORS config from 'my-app': origins=['http://localhost:3000'], credentials=True, enabled=True
WebSocket origin validation for 'my-app': origin=http://localhost:3000
WebSocket origin validation passed for 'my-app': origin=http://localhost:3000, allowed_origins=['http://localhost:3000']
```

### Step 3: Verify Origin Header

Check what origin the browser is sending:
```javascript
// In browser console
console.log('Current origin:', window.location.origin);
// Should match one of your allowed origins
```

### Step 4: Test with curl

Test CORS headers:
```bash
curl -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Content-Type" \
     -X OPTIONS \
     http://localhost:8000/api/data \
     -v
```

Look for:
- `Access-Control-Allow-Origin: http://localhost:3000`
- `Access-Control-Allow-Credentials: true`
- `Access-Control-Allow-Methods: POST`
- `Access-Control-Allow-Headers: Content-Type`

### Step 5: Check Browser Network Tab

1. Open browser DevTools → Network tab
2. Make a request from your frontend
3. Check the request headers:
   - `Origin: http://localhost:3000` ✅
4. Check the response headers:
   - `Access-Control-Allow-Origin: http://localhost:3000` ✅
   - `Access-Control-Allow-Credentials: true` ✅ (if using credentials)

---

## Production Best Practices

### 1. Use Specific Origins

**❌ Don't:**
```json
{
  "cors": {
    "allow_origins": ["*"]
  }
}
```

**✅ Do:**
```json
{
  "cors": {
    "allow_origins": [
      "https://app.yourdomain.com",
      "https://admin.yourdomain.com"
    ]
  }
}
```

### 2. Enable Credentials Only When Needed

Only enable `allow_credentials: true` if you're using:
- Cookie-based authentication
- Authorization headers
- Custom headers that require credentials

### 3. Restrict Methods and Headers

**❌ Don't:**
```json
{
  "cors": {
    "allow_methods": ["*"],
    "allow_headers": ["*"]
  }
}
```

**✅ Do:**
```json
{
  "cors": {
    "allow_methods": ["GET", "POST", "PUT", "DELETE"],
    "allow_headers": ["Content-Type", "Authorization"]
  }
}
```

### 4. Set Appropriate Max Age

For preflight requests, set a reasonable cache time:
```json
{
  "cors": {
    "max_age": 3600
  }
}
```

### 5. Validate Configuration Early

MDB Engine validates CORS configuration during app initialization. Fix any validation errors before deploying:

```
ValueError: CORS config error for 'my-app': Cannot use wildcard origins (*) 
with allow_credentials=true...
```

### 6. Monitor CORS Errors

Set up logging to monitor CORS rejections:
```
WebSocket origin validation failed for 'my-app': origin=https://evil.com 
not in allowed_origins=['https://app.example.com']
```

This helps detect potential attacks or misconfigurations.

---

## Quick Fixes

### Fix 1: Add Missing Origin

**Error:** Origin not in allowed list

**Fix:** Add origin to `manifest.json`:
```json
{
  "cors": {
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ]
  }
}
```

### Fix 2: Remove Wildcard with Credentials

**Error:** Wildcard origin with credentials

**Fix:** Replace wildcard with specific origins:
```json
{
  "cors": {
    "allow_origins": [
      "http://localhost:3000",
      "https://yourdomain.com"
    ],
    "allow_credentials": true
  }
}
```

### Fix 3: Enable CORS

**Error:** CORS not enabled

**Fix:** Set `enabled: true`:
```json
{
  "cors": {
    "enabled": true,
    "allow_origins": ["http://localhost:3000"]
  }
}
```

### Fix 4: Check Origin Exact Match

**Error:** Origin mismatch (protocol/port/hostname)

**Fix:** Ensure exact match including protocol and port:
```json
{
  "cors": {
    "allow_origins": [
      "https://app.yourdomain.com",
      "https://app.yourdomain.com:443"
    ]
  }
}
```

**Note:** MDB Engine normalizes origins, so `https://app.yourdomain.com` and `https://app.yourdomain.com:443` are treated as the same.

---

## Still Having Issues?

1. **Check server logs** for detailed error messages
2. **Verify CORS config** is enabled and correct
3. **Test with curl** to isolate browser issues
4. **Check browser console** for CORS error messages
5. **Verify origin** matches exactly (protocol, hostname, port)
6. **Review this guide** for your specific error

For WebSocket-specific issues, see [WebSocket Troubleshooting Guide](./WEBSOCKET_TROUBLESHOOTING.md).
