# Security Guide

## Table of Contents

1. [Overview](#overview)
2. [Security Architecture](#security-architecture)
3. [Query Security](#query-security)
4. [Resource Limits](#resource-limits)
5. [Collection Name Security](#collection-name-security)
6. [Cross-App Access Control](#cross-app-access-control)
7. [Data Isolation](#data-isolation)
8. [Configuration](#configuration)
9. [Error Handling](#error-handling)
10. [CSRF Protection](#csrf-protection)
11. [Security Best Practices](#security-best-practices)
12. [Security Monitoring](#security-monitoring)
13. [Compliance](#compliance)
14. [Troubleshooting](#troubleshooting)
15. [Future Enhancements](#future-enhancements)
16. [Conclusion](#conclusion)
17. [Appendix A: Security Testing](#appendix-a-security-testing)

## Overview

The MongoDB Engine (`mdb-engine`) provides enterprise-grade security features designed to protect against common vulnerabilities and ensure safe operation in multi-tenant environments. The security model is built on multiple layers of defense, following the principle of defense in depth.

### Security Principles

1. **Defense in Depth**: Multiple security layers protect against different attack vectors
2. **Fail Secure**: Security failures default to blocking access rather than allowing it
3. **Least Privilege**: Apps can only access data they're explicitly authorized to access
4. **Input Validation**: All inputs are validated before processing
5. **Resource Protection**: Automatic limits prevent resource exhaustion attacks
6. **Audit Trail**: Security events are logged for monitoring and forensics

### Security Layers

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                     │
│  (Your application code using mdb-engine)                │
└────────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────────────┐
│              Scoped Database Layer                        │
│  • Collection name validation                            │
│  • App-level authentication (token verification)          │
│  • Cross-app access control                               │
│  • Scope validation                                       │
└────────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────────────┐
│            Query Security Layer                           │
│  • Dangerous operator blocking                            │
│  • Query complexity limits                                │
│  • Regex validation                                       │
└────────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────────────┐
│          Resource Limits Layer                            │
│  • Query timeouts                                         │
│  • Result size limits                                     │
│  • Document size validation                               │
└────────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────────────┐
│            Data Isolation Layer                           │
│  • Automatic app_id filtering                             │
│  • Write scope enforcement                                │
│  • Read scope enforcement                                 │
└────────────────────┬──────────────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────────────┐
│              MongoDB Layer                                │
│  (Underlying MongoDB database)                            │
└────────────────────────────────────────────────────────────┘
```

## Security Architecture

### Component Overview

The security system consists of several key components:

1. **QueryValidator**: Validates all queries for security before execution
2. **ResourceLimiter**: Enforces resource limits on all operations
3. **ScopedMongoWrapper**: Provides scoped database access with validation
4. **ScopedCollectionWrapper**: Wraps collections with security and scoping
5. **Collection Name Validator**: Validates collection names for security

### Security Flow

```
User Query
    │
    ├─► App Token Verification (if app has stored secret)
    │   └─► Valid? ──► Continue
    │       Invalid? ──► Raise ValueError
    │
    ├─► Collection Name Validation
    │   └─► Valid? ──► Continue
    │       Invalid? ──► Raise ValueError
    │
    ├─► Cross-App Access Check
    │   └─► Authorized? ──► Continue
    │       Unauthorized? ──► Raise ValueError
    │
    ├─► Query Validation
    │   ├─► Dangerous Operators? ──► Raise QueryValidationError
    │   ├─► Query Depth OK? ──► Continue
    │   │   Too Deep? ──► Raise QueryValidationError
    │   ├─► Regex Valid? ──► Continue
    │   │   Invalid? ──► Raise QueryValidationError
    │   └─► Pipeline Valid? ──► Continue
    │       Invalid? ──► Raise QueryValidationError
    │
    ├─► Resource Limits
    │   ├─► Timeout Set? ──► Validate/Cap
    │   ├─► Result Limit OK? ──► Continue/Cap
    │   └─► Document Size OK? ──► Continue
    │       Too Large? ──► Raise ResourceLimitExceeded
    │
    ├─► Data Scoping
    │   └─► Inject app_id filter
    │
    └─► Execute Query
```

## Query Security

### Dangerous Operator Blocking

The engine automatically blocks MongoDB operators that allow code execution or pose security risks:

#### Blocked Operators

| Operator | Reason | Risk Level |
|----------|--------|------------|
| `$where` | Allows JavaScript execution in queries | **CRITICAL** |
| `$eval` | Evaluates JavaScript code (deprecated) | **CRITICAL** |
| `$function` | Defines JavaScript functions | **CRITICAL** |
| `$accumulator` | Can be abused for code execution | **HIGH** |

#### How It Works

The `QueryValidator` recursively scans all query filters and aggregation pipelines, checking for dangerous operators at any nesting level:

```python
# ❌ These queries will be blocked:
db.collection.find({"$where": "this.status === 'active'"})
db.collection.find({"nested": {"$where": "true"}})
db.collection.aggregate([{"$match": {"$eval": "code"}}])

# ✅ Safe alternatives:
db.collection.find({"status": "active"})
db.collection.find({"age": {"$gt": 18, "$lt": 65}})
db.collection.find({"tags": {"$in": ["red", "blue"]}})
```

#### Implementation Details

- **Recursive Scanning**: The validator checks nested dictionaries and arrays
- **Path Tracking**: Error messages include the JSON path where the operator was found
- **Early Detection**: Validation happens before query execution
- **Comprehensive Coverage**: Works for filters, aggregation pipelines, and nested queries

### Query Complexity Limits

To prevent resource exhaustion and performance issues, the engine enforces complexity limits:

#### Query Depth Limits

**Default**: 10 levels of nesting  
**Purpose**: Prevents deeply nested queries that could cause performance issues

```python
# ❌ Too deeply nested (exceeds depth limit):
deep_query = {
    "level1": {
        "level2": {
            "level3": {
                "level4": {
                    "level5": {
                        "level6": {
                            "level7": {
                                "level8": {
                                    "level9": {
                                        "level10": {
                                            "level11": {"value": 1}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

# ✅ Valid depth:
valid_query = {
    "level1": {
        "level2": {
            "level3": {"value": 1}
        }
    }
}
```

#### Pipeline Stage Limits

**Default**: 50 stages per aggregation pipeline  
**Purpose**: Prevents overly complex aggregation pipelines

```python
# ❌ Too many stages:
pipeline = [{"$match": {}}] * 100  # Exceeds 50 stage limit

# ✅ Valid pipeline:
pipeline = [
    {"$match": {"status": "active"}},
    {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}},
    {"$limit": 10}
]
```

#### Sort Field Limits

**Default**: 10 fields per sort specification  
**Purpose**: Prevents excessive sorting operations

```python
# ❌ Too many sort fields:
sort_fields = [(f"field{i}", 1) for i in range(15)]  # Exceeds 10 field limit

# ✅ Valid sort:
sort_fields = [("field1", 1), ("field2", -1), ("field3", 1)]
```

### Regex Validation

The engine validates regex patterns to prevent ReDoS (Regular Expression Denial of Service) attacks:

#### Regex Limits

- **Maximum Length**: 1000 characters
- **Maximum Complexity**: 50 (based on quantifiers, alternations, nested groups)

#### Complexity Calculation

The complexity score is calculated using heuristics:

```python
complexity = 0
complexity += len(re.findall(r"[*+?{]", pattern))  # Quantifiers
complexity += len(re.findall(r"\|", pattern))      # Alternations
complexity += len(nested_groups)                   # Nested groups
complexity += len(re.findall(r"\(\?[=!<>]", pattern))  # Lookahead/lookbehind
```

#### Examples

```python
# ❌ Overly complex regex:
complex_pattern = "(a+)*" * 20  # Many quantifiers, high complexity

# ❌ Overly long regex:
long_pattern = "a" * 1001  # Exceeds 1000 character limit

# ✅ Valid regex patterns:
simple_pattern = "^test$"
email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
```

#### ReDoS Prevention

ReDoS attacks exploit regex patterns that can cause exponential backtracking:

```python
# Dangerous pattern (blocked):
dangerous = "(a+)+b"  # Can cause exponential backtracking

# Safe pattern:
safe = "^[a-z]+$"  # Linear time complexity
```

## Resource Limits

### Query Timeouts

All queries automatically have timeouts enforced to prevent long-running queries from consuming resources:

#### Default Timeouts

- **Default Timeout**: 30 seconds (`maxTimeMS=30000`)
- **Maximum Allowed Timeout**: 5 minutes (`maxTimeMS=300000`)
- **Automatic Enforcement**: Timeouts are added to all queries if not specified

#### How It Works

```python
# Timeout is automatically added:
cursor = db.collection.find({"status": "active"})
# Query will timeout after 30 seconds by default

# Custom timeout (capped to 5 minutes):
cursor = db.collection.find({"status": "active"}, maxTimeMS=60000)
# Query will timeout after 60 seconds

# Excessive timeout is capped:
cursor = db.collection.find({"status": "active"}, maxTimeMS=600000)
# Query timeout is capped to 5 minutes (300000ms)
```

#### Timeout Enforcement

The `ResourceLimiter` enforces timeouts on:

- `find()` - Query operations
- `find_one()` - Single document queries
- `count_documents()` - Count operations
- `aggregate()` - Aggregation pipelines
- `insert_one()` - Single document inserts
- `insert_many()` - Bulk inserts
- `update_one()` - Single document updates
- `update_many()` - Bulk updates
- `delete_one()` - Single document deletes
- `delete_many()` - Bulk deletes

### Result Size Limits

To prevent excessive memory usage, result sizes are automatically limited:

#### Limits

- **Maximum Result Size**: 10,000 documents per query
- **Maximum Batch Size**: 1,000 documents per cursor batch
- **Automatic Capping**: Limits are capped, not rejected (for backward compatibility)

#### Examples

```python
# Limit is automatically capped:
cursor = db.collection.find({}, limit=20000)
# Limit is capped to 10,000 documents

# Batch size is also limited:
cursor = db.collection.find({}, batch_size=2000)
# Batch size is capped to 1,000 documents

# Use pagination for larger result sets:
async def get_all_documents(collection, page_size=1000):
    cursor = collection.find({}, limit=page_size)
    async for doc in cursor:
        yield doc
    # Continue with skip/limit for pagination
```

### Document Size Limits

Documents are validated before insert to prevent oversized documents:

#### Limits

- **Maximum Document Size**: 16MB (MongoDB limit)
- **Pre-insert Validation**: Documents are checked before sending to MongoDB
- **Clear Error Messages**: Errors indicate which document exceeded the limit

#### Size Estimation

The engine estimates document size by serializing to JSON and adding overhead:

```python
# Size estimation algorithm:
json_str = json.dumps(document, default=str)
size_bytes = len(json_str.encode("utf-8"))
estimated_size = int(size_bytes * 1.1)  # Add 10% overhead for BSON
```

#### Examples

```python
# ❌ Oversized document:
large_doc = {"data": "x" * (20 * 1024 * 1024)}  # 20MB
await db.collection.insert_one(large_doc)
# Raises ResourceLimitExceeded

# ✅ Valid document:
normal_doc = {"name": "John", "age": 30, "data": "some data"}
await db.collection.insert_one(normal_doc)  # Works fine

# ✅ Use GridFS for large data:
from gridfs import GridFS
fs = GridFS(db.database)
file_id = fs.put(large_data, filename="large_file.bin")
doc = {"name": "John", "file_ref": file_id}
await db.collection.insert_one(doc)
```

## Collection Name Security

### Validation Rules

Collection names are strictly validated to prevent security vulnerabilities:

#### Format Requirements

- **Characters**: Alphanumeric, underscore (`_`), dot (`.`), hyphen (`-`)
- **Length**: 1-255 characters
- **Start Character**: Must start with a letter or underscore (not a number)
- **Case Sensitivity**: MongoDB collection names are case-sensitive

#### Reserved Names

The following collection names are blocked:

- `apps_config` - Engine internal (app registration)
- `_mdb_engine_app_secrets` - Engine internal (encrypted app secrets)
- Any name starting with `system` - MongoDB system collections
- Any name starting with `admin` - MongoDB admin collections
- Any name starting with `config` - MongoDB config collections
- Any name starting with `local` - MongoDB local collections

#### Path Traversal Protection

Path traversal attempts are blocked:

```python
# ❌ These are blocked:
db["../other_collection"]  # Path traversal
db["../../etc/passwd"]    # Path traversal
db["collection/name"]     # Invalid character (/)
db["collection\\name"]    # Invalid character (\)

# ✅ Valid collection names:
db.users
db.user_profiles
db.product_catalog_v2
db["my-collection"]       # Hyphen allowed
db["my.collection"]       # Dot allowed
```

#### Implementation

The validation uses regex patterns and explicit checks:

```python
# Validation pattern:
COLLECTION_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$")

# Reserved prefix check:
for prefix in RESERVED_COLLECTION_PREFIXES:
    if name.startswith(prefix):
        raise ValueError(f"Collection name cannot start with '{prefix}'")
```

## App-Level Authentication

### Overview

MDB_ENGINE implements secure app-level authentication using **envelope encryption** to protect app secrets. Each app has a unique secret token that must be provided when accessing the database.

**Key Features:**
- Envelope encryption for secret storage
- Runtime token verification
- Manifest-level authorization
- Constant-time comparison (timing attack prevention)
- Comprehensive audit logging

### Architecture

App secrets are encrypted using a two-layer encryption model:

1. **Master Key (MK)**: Encrypts Data Encryption Keys (DEKs)
2. **Data Encryption Key (DEK)**: Encrypts app secrets (one per app)
3. **App Secret**: Plaintext secret, encrypted with DEK

This provides defense-in-depth: even if DEKs are compromised, they cannot be decrypted without the master key.

### Internal Secure Collection

Secrets are stored in the `_mdb_engine_app_secrets` collection:

- **Access**: Only accessible via raw MongoDB client (bypasses scoped wrapper)
- **Schema**: Stores encrypted secret, encrypted DEK, algorithm, timestamps
- **Security**: Collection is reserved and cannot be accessed by apps

### How It Works

**Registration:**
1. App is registered via `register_app(manifest)`
2. Engine generates random 256-bit secret
3. Secret is encrypted using envelope encryption
4. Encrypted secret is stored in `_mdb_engine_app_secrets`

**Runtime Access:**
1. App provides `app_token` to `get_scoped_db()` (or auto-retrieved if not in environment)
2. Engine retrieves encrypted secret from database
3. Engine decrypts secret using master key
4. Engine compares provided token with decrypted secret (constant-time)
5. If match, returns scoped database; if mismatch, raises `ValueError`

**Automatic Secret Management:**
- **First Run**: Secret generated → Encrypted → Stored → Auto-retrieved from database
- **Subsequent Runs**: Secret auto-retrieved from encrypted storage if not in environment
- **Optional Optimization**: Store secrets in environment variables to avoid DB lookup on every request
- **Zero Configuration**: Works automatically - no manual secret copying required

### Master Key Management

**Development:**
```bash
# Generate master key
python -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'

# Store in .env (gitignored)
MDB_ENGINE_MASTER_KEY="<generated-key>"
```

**Production:**
- Use key management service (AWS KMS, HashiCorp Vault)
- Store master key securely
- Rotate master keys regularly

### Security Benefits

- **App Identity Verification**: Secrets prove which app is making the request
- **Encrypted Storage**: Secrets never stored in plaintext
- **Constant-Time Comparison**: Prevents timing attacks
- **Audit Trail**: All verification attempts logged
- **Fail Secure**: Invalid tokens → access denied

For detailed documentation, see [App Authentication Guide](APP_AUTHENTICATION.md).

## Cross-App Access Control

### Authorization Model

Cross-app collection access is strictly controlled through the `read_scopes` mechanism with app-level authentication:

#### How It Works

1. **App Registration**: Each app is registered with a unique `slug` and encrypted secret
   - Secret is automatically generated (256-bit random)
   - Secret is encrypted using envelope encryption
   - Encrypted secret is stored in `_mdb_engine_app_secrets` collection
2. **Manifest Configuration**: Apps declare `data_access.read_scopes` in manifest
   - `read_scopes`: List of apps this app can read from
   - `write_scope`: App this app can write to (defaults to own slug)
   - Validated at registration time
3. **Token Verification**: Apps must provide valid `app_token` to access database
   - Token is automatically retrieved from encrypted storage if not in environment
   - Token is verified using constant-time comparison (prevents timing attacks)
   - Verification happens before any database access
4. **Read Scopes**: Apps specify which other apps they can read from
   - Must be declared in manifest `read_scopes`
   - Validated on every `get_scoped_db()` call
   - Unauthorized access attempts are blocked and logged
5. **Write Scope**: Apps can only write to their own collections
   - All writes are tagged with `app_id` matching the app's `write_scope`
   - Prevents apps from writing to other apps' collections
6. **Access Validation**: All cross-app access attempts are validated
   - Token verification (app identity)
   - Scope validation (authorized apps)
   - Collection name validation (security)
   - All access is logged for audit

#### Example Configuration

```python
# App "my_app" can read from itself and "shared_app"
# Must provide app_token for authentication
db = engine.get_scoped_db(
    "my_app",
    app_token=os.getenv("MY_APP_SECRET"),  # Required for apps with secrets
    read_scopes=["my_app", "shared_app"],  # Optional - uses manifest if not provided
    write_scope="my_app"
)

# ✅ This works (authorized):
collection = db.get_collection("shared_app_data")

# ❌ This fails (unauthorized):
collection = db.get_collection("other_app_data")
# Raises ValueError: Access to collection 'other_app_data' not authorized
```

#### Manifest-Level Configuration

Declare cross-app access in manifest:

```json
{
  "slug": "my_app",
  "data_access": {
    "read_scopes": ["my_app", "shared_app"],
    "write_scope": "my_app",
    "cross_app_policy": "explicit"
  }
}
```

#### Access Validation Flow

```
Collection Access Request
    │
    ├─► Verify app_token (if app has stored secret)
    │   ├─► Valid → Continue
    │   └─► Invalid → Raise ValueError
    │
    ├─► Extract app slug from collection name
    │   (e.g., "shared_app_data" → "shared_app")
    │
    ├─► Check if app is in read_scopes (from manifest)
    │   ├─► Yes → Allow access
    │   └─► No → Block access and log warning
    │
    └─► Log access (authorized or unauthorized)
```

#### Security Benefits

- **App Identity Verification**: Tokens verify which app is making the request
- **Explicit Authorization**: Apps must explicitly grant access in manifest
- **Audit Trail**: All cross-app access is logged with app identity
- **Default Deny**: Unauthorized access is blocked by default
- **Scope Isolation**: Apps cannot accidentally access other apps' data
- **Defense in Depth**: Multiple layers (encryption, manifest config, runtime validation)

## Data Isolation

### Automatic App ID Filtering

The engine automatically filters all queries by `app_id` to ensure data isolation:

#### Read Operations

All read operations automatically include an `app_id` filter:

```python
# User query:
db.collection.find({"status": "active"})

# Actual query sent to MongoDB:
{
    "$and": [
        {"status": "active"},
        {"app_id": {"$in": ["my_app"]}}
    ]
}
```

#### Write Operations

All write operations automatically add `app_id`:

```python
# User insert:
await db.collection.insert_one({"name": "John", "age": 30})

# Actual document inserted:
{
    "name": "John",
    "age": 30,
    "app_id": "my_app"  # Automatically added
}
```

#### Multi-App Read Scopes

When an app has multiple read scopes, queries include all authorized apps:

```python
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_app"],
    write_scope="my_app"
)

# Query includes both apps:
{
    "$and": [
        {"status": "active"},
        {"app_id": {"$in": ["my_app", "shared_app"]}}
    ]
}
```

### Scope Validation

The engine validates scopes when creating scoped databases:

#### Validation Rules

- **read_scopes**: Must be a non-empty list of strings
- **write_scope**: Must be a non-empty string
- **write_scope in read_scopes**: Write scope must be included in read scopes

#### Examples

```python
# ✅ Valid:
db = engine.get_scoped_db("my_app")  # Defaults to read_scopes=["my_app"]

# ✅ Valid:
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["my_app", "shared_app"],
    write_scope="my_app"
)

# ❌ Invalid (empty read_scopes):
db = engine.get_scoped_db("my_app", read_scopes=[])

# ❌ Invalid (write_scope not in read_scopes):
db = engine.get_scoped_db(
    "my_app",
    read_scopes=["other_app"],
    write_scope="my_app"
)
```

## Configuration

### Custom Validators and Limiters

You can customize security settings per app by providing custom validators and limiters:

#### Creating Custom Validators

```python
from mdb_engine.database.query_validator import QueryValidator

# Create custom validator with relaxed limits
custom_validator = QueryValidator(
    max_depth=15,  # Allow deeper nesting (default: 10)
    max_pipeline_stages=100,  # Allow more pipeline stages (default: 50)
    max_regex_length=2000,  # Allow longer regex patterns (default: 1000)
    max_regex_complexity=100,  # Allow more complex regex (default: 50)
    dangerous_operators={"$custom"},  # Block additional operators
)

# Use with scoped database
db = engine.get_scoped_db("my_app")
# Note: Currently validators are created per ScopedMongoWrapper
# Future: Support per-app configuration via manifest
```

#### Creating Custom Limiters

```python
from mdb_engine.database.resource_limiter import ResourceLimiter

# Create custom limiter with different limits
custom_limiter = ResourceLimiter(
    default_timeout_ms=60000,  # 60 second default timeout (default: 30000)
    max_timeout_ms=600000,  # 10 minute maximum (default: 300000)
    max_result_size=50000,  # Allow larger result sets (default: 10000)
    max_batch_size=2000,  # Larger batch sizes (default: 1000)
    max_document_size=32 * 1024 * 1024,  # 32MB limit (default: 16MB)
)

# Use with scoped database
db = engine.get_scoped_db("my_app")
# Note: Currently limiters are created per ScopedMongoWrapper
# Future: Support per-app configuration via manifest
```

### Per-App Configuration (Future)

Future versions will support per-app security configuration via manifest:

```json
{
  "slug": "my_app",
  "security": {
    "query_validation": {
      "enabled": true,
      "max_depth": 15,
      "max_pipeline_stages": 100,
      "dangerous_operators": ["$custom"]
    },
    "resource_limits": {
      "default_timeout_ms": 60000,
      "max_result_size": 50000,
      "max_batch_size": 2000
    }
  }
}
```

## Error Handling

### QueryValidationError

Raised when queries fail validation:

#### Attributes

- `message`: Human-readable error message
- `query_type`: Type of query that failed (`filter`, `pipeline`, `regex`, `sort`)
- `operator`: Dangerous operator that was found (if applicable)
- `path`: JSON path where the issue was found (if applicable)
- `context`: Additional context information

#### Example Usage

```python
from mdb_engine.exceptions import QueryValidationError

try:
    db.collection.find({"$where": "true"})
except QueryValidationError as e:
    print(f"Query validation failed: {e}")
    print(f"Query type: {e.query_type}")
    print(f"Dangerous operator: {e.operator}")
    print(f"Path: {e.path}")
    print(f"Context: {e.context}")
```

### ResourceLimitExceeded

Raised when resource limits are exceeded:

#### Attributes

- `message`: Human-readable error message
- `limit_type`: Type of limit that was exceeded (`timeout`, `result_size`, `document_size`)
- `limit_value`: The limit value that was exceeded
- `actual_value`: The actual value that exceeded the limit
- `context`: Additional context information

#### Example Usage

```python
from mdb_engine.exceptions import ResourceLimitExceeded

try:
    large_doc = {"data": "x" * (20 * 1024 * 1024)}
    await db.collection.insert_one(large_doc)
except ResourceLimitExceeded as e:
    print(f"Resource limit exceeded: {e}")
    print(f"Limit type: {e.limit_type}")
    print(f"Limit value: {e.limit_value}")
    print(f"Actual value: {e.actual_value}")
    print(f"Context: {e.context}")
```

## CSRF Protection

Cross-Site Request Forgery (CSRF) protection is automatically enabled for apps using shared authentication mode. The implementation uses the **double-submit cookie pattern**.

### How It Works

1. **Token Generation**: A CSRF token is generated and set as a cookie (`csrf_token`)
2. **Token Submission**: State-changing requests must include the token in the `X-CSRF-Token` header
3. **Token Validation**: The server validates the header matches the cookie value

### Configuration

CSRF protection is configured in your manifest.json:

```json
{
  "auth": {
    "mode": "shared",
    "csrf_protection": true
  }
}
```

Advanced configuration options:

```json
{
  "auth": {
    "csrf_protection": {
      "enabled": true,
      "exempt_routes": ["/api/webhooks/*", "/health"],
      "token_ttl": 3600,
      "rotate_tokens": false
    }
  }
}
```

### Frontend Integration

#### Reading the CSRF Token

```javascript
// Helper function to read cookies
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}
```

#### Including in Requests

All POST, PUT, and DELETE requests must include the CSRF token:

```javascript
// Example: POST request with CSRF token
async function submitData(data) {
    const response = await fetch('/api/data', {
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

// Example: DELETE request with CSRF token
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

### Logout Must Be POST

For security reasons, logout endpoints should use POST method with CSRF protection, not GET:

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

Backend implementation:

```python
from fastapi.responses import JSONResponse

@app.post("/logout")
async def logout(request: Request):
    """Logout must be POST with CSRF token."""
    response = JSONResponse({"success": True, "redirect": "/login"})
    response = await logout_user(request, response)
    return response
```

### AJAX Authentication Pattern

Login and registration endpoints should return JSON for JavaScript frontends:

```python
@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    """Login returning JSON for AJAX frontend."""
    result = await authenticate_user(email, password)
    
    if result["success"]:
        json_response = JSONResponse({
            "success": True,
            "redirect": "/dashboard"
        })
        # Copy auth cookies
        for key, value in result["response"].headers.items():
            if key.lower() == "set-cookie":
                json_response.headers.append(key, value)
        return json_response
    
    return JSONResponse(
        {"success": False, "detail": result.get("error", "Login failed")},
        status_code=401
    )
```

### Error Responses

When CSRF validation fails, the server returns a 403 status:

```json
{
  "detail": "Invalid or missing CSRF token"
}
```

Handle this in your frontend:

```javascript
async function makeRequest(url, options) {
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
        if (data.detail && data.detail.includes('CSRF')) {
            // Token may have expired - refresh the page
            window.location.reload();
            return;
        }
    }
    
    return response;
}
```

### Best Practices

1. **Always use POST for state changes** - Never use GET for logout, delete, or update operations
2. **Include credentials** - Use `credentials: 'same-origin'` in fetch requests
3. **Handle 403 errors** - Refresh the page or prompt the user if CSRF token expires
4. **Don't expose tokens in URLs** - Always use headers, never query parameters
5. **Test CSRF protection** - Verify that requests without valid tokens are rejected

## Security Best Practices

### For Developers

1. **Always Use Scoped Databases**
   - Never access raw MongoDB clients
   - Always use `engine.get_scoped_db()` for database access

2. **Validate User Inputs**
   - Validate all inputs before building queries
   - Use parameterized queries (never string concatenation)
   - Sanitize user-provided collection names

3. **Use Safe Query Patterns**
   - Avoid dangerous operators (they're blocked anyway, but don't try to use them)
   - Use indexes for performance
   - Set appropriate limits for queries

4. **Handle Errors Gracefully**
   - Catch `QueryValidationError` and provide user-friendly messages
   - Catch `ResourceLimitExceeded` and suggest alternatives
   - Log security violations for monitoring

5. **Monitor Query Performance**
   - Review slow queries and optimize indexes
   - Use appropriate timeouts for long-running queries
   - Paginate large result sets

6. **Test Security Features**
   - Test that dangerous operators are blocked
   - Test that resource limits are enforced
   - Test that cross-app access is controlled

### For Administrators

1. **Review Security Logs**
   - Monitor for blocked queries and security violations
   - Review cross-app access patterns
   - Investigate unusual access attempts

2. **Tune Resource Limits**
   - Adjust limits based on application needs
   - Monitor resource usage patterns
   - Balance security with usability

3. **Monitor Query Performance**
   - Track query execution times
   - Identify slow queries
   - Optimize indexes based on query patterns

4. **Review Cross-App Access**
   - Regularly audit `read_scopes` configurations
   - Remove unnecessary cross-app access
   - Document cross-app access requirements

5. **Keep Engine Updated**
   - Stay current with security updates
   - Review security advisories
   - Test updates in staging before production

### For Security Teams

1. **Audit Logs**
   - Review security event logs regularly
   - Look for patterns indicating attacks
   - Investigate suspicious activity

2. **Penetration Testing**
   - Test query validation and resource limits
   - Attempt to bypass security controls
   - Verify data isolation between apps

3. **Compliance**
   - Ensure security features meet compliance requirements
   - Document security controls
   - Maintain audit trails

4. **Incident Response**
   - Have procedures for handling security violations
   - Document security incidents
   - Review and improve security controls

5. **Documentation**
   - Keep security documentation up to date
   - Document security configurations
   - Maintain runbooks for security operations

## Security Monitoring

### Logging

Security events are automatically logged at appropriate levels:

#### Log Levels

- **WARNING**: Security violations (blocked queries, unauthorized access)
- **INFO**: Authorized cross-app access
- **DEBUG**: Detailed validation information

#### Example Log Entries

```
WARNING: Security: Dangerous operator '$where' detected in query at path 'filter'
WARNING: Security: Invalid collection name attempted. Name: 'system_users', App: 'my_app'
WARNING: Security: Unauthorized cross-app access attempt. Collection: 'other_app_data', Target app: 'other_app'
INFO: Cross-app access authorized. Collection: 'shared_app_data', From app: 'my_app', To app: 'shared_app'
```

#### Logging Configuration

Configure logging in your application:

```python
import logging

# Configure security logging
security_logger = logging.getLogger("mdb_engine.database.scoped_wrapper")
security_logger.setLevel(logging.WARNING)

# Configure query validation logging
validation_logger = logging.getLogger("mdb_engine.database.query_validator")
validation_logger.setLevel(logging.INFO)
```

### Metrics

Query performance and security metrics are tracked:

#### Available Metrics

- Query execution times
- Resource limit violations
- Validation failures
- Security event counts
- Cross-app access patterns

#### Metrics Collection

Metrics are collected via the observability system:

```python
from mdb_engine.observability import record_operation

# Metrics are automatically recorded for:
# - database.find
# - database.find_one
# - database.insert_one
# - database.insert_many
# - database.update_one
# - database.update_many
# - database.delete_one
# - database.delete_many
# - database.count_documents
# - database.aggregate
```

## Compliance

### SOC 2

The engine supports SOC 2 compliance requirements:

- ✅ **Access Controls**: Collection name validation, cross-app access control
- ✅ **Query Validation**: Dangerous operator blocking, complexity limits
- ✅ **Resource Limits**: Timeouts, result size limits, document size validation
- ✅ **Audit Logging**: Security events logged with context
- ✅ **Data Isolation**: Automatic app-level scoping

### GDPR

The engine supports GDPR compliance requirements:

- ✅ **Data Isolation**: Automatic app-level scoping ensures data separation
- ✅ **Access Controls**: Cross-app access restrictions
- ⚠️ **Right to Deletion**: Future feature (see Future Enhancements)
- ⚠️ **Data Export**: Future feature (see Future Enhancements)
- ⚠️ **Data Portability**: Future feature (see Future Enhancements)

### OWASP Top 10

The engine addresses OWASP Top 10 vulnerabilities:

- ✅ **A03:2021 – Injection**: NoSQL injection prevention via query validation
- ✅ **A04:2021 – Insecure Design**: Defense in depth with multiple security layers
- ✅ **A05:2021 – Security Misconfiguration**: Secure defaults, validation, limits
- ✅ **A06:2021 – Vulnerable Components**: Regular updates, dependency management
- ✅ **A09:2021 – Security Logging**: Comprehensive security event logging

### HIPAA (Healthcare)

For healthcare applications:

- ✅ **Access Controls**: Strict access controls and data isolation
- ✅ **Audit Logging**: Comprehensive audit trails
- ⚠️ **Encryption at Rest**: Future feature (see Future Enhancements)
- ⚠️ **Encryption in Transit**: TLS/SSL enforcement (see Future Enhancements)

## Troubleshooting

### Query Validation Errors

#### Error: `QueryValidationError: Dangerous operator '$where' is not allowed`

**Cause**: Query contains a blocked dangerous operator.

**Solution**: Use safe MongoDB operators instead:

```python
# Instead of:
db.collection.find({"$where": "this.status === 'active'"})

# Use:
db.collection.find({"status": "active"})
```

#### Error: `QueryValidationError: Query exceeds maximum nesting depth`

**Cause**: Query is too deeply nested.

**Solution**: Simplify the query structure:

```python
# Instead of deeply nested query:
deep_query = {"level1": {"level2": {"level3": ...}}}

# Use flatter structure:
flat_query = {"level1.value": 123, "level2.value": 456}
```

#### Error: `QueryValidationError: Aggregation pipeline exceeds maximum stages`

**Cause**: Pipeline has too many stages.

**Solution**: Break pipeline into multiple queries or reduce stages:

```python
# Instead of one large pipeline:
pipeline = [{"$match": {}}] * 100

# Break into smaller pipelines:
pipeline1 = [{"$match": {...}}, {"$group": {...}}]
pipeline2 = [{"$match": {...}}, {"$sort": {...}}]
```

### Resource Limit Errors

#### Error: `ResourceLimitExceeded: Result limit exceeds maximum`

**Cause**: Query limit exceeds maximum allowed.

**Solution**: Reduce limit or use pagination:

```python
# Instead of:
docs = await db.collection.find({}, limit=20000).to_list(None)

# Use pagination:
async def get_all_docs(collection, page_size=1000):
    skip = 0
    while True:
        docs = await collection.find({}).skip(skip).limit(page_size).to_list(None)
        if not docs:
            break
        for doc in docs:
            yield doc
        skip += page_size
```

#### Error: `ResourceLimitExceeded: Document size exceeds maximum`

**Cause**: Document exceeds 16MB MongoDB limit.

**Solution**: Split large documents or use GridFS:

```python
# Instead of storing large data in document:
large_doc = {"data": huge_string}

# Use references or GridFS:
from gridfs import GridFS
fs = GridFS(db.database)
file_id = fs.put(large_data, filename="large_file.bin")
doc = {"name": "Document", "file_ref": file_id}
await db.collection.insert_one(doc)
```

### Collection Name Errors

#### Error: `ValueError: Collection name cannot start with 'system'`

**Cause**: Collection name uses reserved prefix.

**Solution**: Use a different name:

```python
# Instead of:
db.system_users

# Use:
db.users
db.app_users
```

### App Authentication Errors

#### Error: `ValueError: Invalid app token for 'my_app'`

**Cause**: Provided app token doesn't match stored encrypted secret.

**Solution**: Verify secret matches stored value. See [App Authentication Guide](APP_AUTHENTICATION.md) for details.

```bash
# Retrieve current secret (if you have access)
# Update environment variable
export MY_APP_SECRET="<correct-secret>"
```

#### Error: `ValueError: App token required for 'my_app'`

**Cause**: App has stored secret but `app_token` not provided.

**Solution**: Provide `app_token` parameter:

```python
# Instead of:
db = engine.get_scoped_db("my_app")

# Use:
db = engine.get_scoped_db("my_app", app_token=os.getenv("MY_APP_SECRET"))
```

#### Error: `ValueError: Master key not found`

**Cause**: `MDB_ENGINE_MASTER_KEY` environment variable not set.

**Solution**: Generate and set master key:

```bash
# Generate master key
python -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'

# Set environment variable
export MDB_ENGINE_MASTER_KEY="<generated-key>"
```

### Cross-App Access Errors

#### Error: `ValueError: Access to collection 'other_app_data' not authorized`

**Cause**: App is not authorized to access the collection (not in `read_scopes`).

**Solution**: Add the app to manifest `read_scopes`:

```json
// Update manifest.json
{
  "data_access": {
    "read_scopes": ["my_app", "other_app"]  // Add other_app
  }
}

// Re-register app
await engine.register_app(manifest)
```

#### Error: `ValueError: App 'my_app' not authorized to read from 'other_app'`

**Cause**: Requested `read_scopes` include app not in manifest authorization.

**Solution**: Update manifest or remove unauthorized scope from request:

```python
# Option 1: Update manifest read_scopes
# Option 2: Remove unauthorized scope from request
db = engine.get_scoped_db(
    "my_app",
    app_token="secret",
    read_scopes=["my_app"],  # Remove other_app if not authorized
)
```

## Future Enhancements

This section outlines planned security enhancements for future versions of mdb-engine.

### Phase 1: Enhanced Query Security

#### Query Rate Limiting

**Status**: Planned  
**Priority**: High  
**Description**: Per-app query rate limits to prevent DoS attacks.

**Implementation**:
- Track query rates per app
- Enforce rate limits (e.g., 1000 queries/minute per app)
- Configurable limits per app
- Automatic throttling when limits exceeded

**Configuration**:
```json
{
  "security": {
    "rate_limiting": {
      "enabled": true,
      "queries_per_minute": 1000,
      "burst_limit": 100
    }
  }
}
```

#### Query Pattern Analysis

**Status**: Planned  
**Priority**: Medium  
**Description**: Analyze query patterns to detect anomalies and potential attacks.

**Features**:
- Detect unusual query patterns
- Identify potential injection attempts
- Alert on suspicious activity
- Machine learning-based detection

#### Advanced Regex Validation

**Status**: Planned  
**Priority**: Medium  
**Description**: More sophisticated regex validation using formal analysis.

**Features**:
- Formal regex complexity analysis
- Detect exponential backtracking patterns
- Suggest safer alternatives
- Configurable complexity thresholds

### Phase 2: Network Security

#### TLS/SSL Enforcement

**Status**: Planned  
**Priority**: High  
**Description**: Require encrypted connections and validate certificates.

**Implementation**:
- Enforce TLS/SSL for all connections
- Certificate validation
- Certificate pinning support
- Configurable TLS versions

**Configuration**:
```json
{
  "security": {
    "tls": {
      "enabled": true,
      "require_tls": true,
      "min_tls_version": "1.2",
      "certificate_validation": true,
      "certificate_pinning": false
    }
  }
}
```

#### IP Whitelisting

**Status**: Planned  
**Priority**: Medium  
**Description**: Network access control via IP whitelisting.

**Features**:
- Per-app IP whitelists
- CIDR block support
- Dynamic IP management
- Integration with firewall rules

**Configuration**:
```json
{
  "security": {
    "network": {
      "ip_whitelist": {
        "enabled": true,
        "allowed_ips": ["192.168.1.0/24", "10.0.0.0/8"]
      }
    }
  }
}
```

#### Connection Encryption

**Status**: Planned  
**Priority**: High  
**Description**: End-to-end encryption for database connections.

**Features**:
- Encrypted connection pools
- Key rotation support
- Encryption at rest integration
- Performance-optimized encryption

### Phase 3: Advanced Access Control

#### Role-Based Access Control (RBAC)

**Status**: Planned  
**Priority**: Medium  
**Description**: Fine-grained access control based on roles and permissions.

**Features**:
- Role definitions
- Permission management
- Role assignment
- Permission inheritance

**Configuration**:
```json
{
  "security": {
    "rbac": {
      "enabled": true,
      "roles": {
        "admin": ["read", "write", "delete"],
        "user": ["read"],
        "editor": ["read", "write"]
      }
    }
  }
}
```

#### Attribute-Based Access Control (ABAC)

**Status**: Planned  
**Priority**: Low  
**Description**: Access control based on attributes and policies.

**Features**:
- Policy-based access control
- Attribute evaluation
- Dynamic access decisions
- Policy management

#### Time-Based Access Control

**Status**: Planned  
**Priority**: Low  
**Description**: Access control based on time windows.

**Features**:
- Time-based access windows
- Scheduled access
- Timezone support
- Access expiration

### Phase 4: Data Protection

#### Encryption at Rest

**Status**: Planned  
**Priority**: High  
**Description**: Encrypt data stored in MongoDB.

**Features**:
- Transparent data encryption
- Key management integration
- Encryption key rotation
- Performance optimization

**Configuration**:
```json
{
  "security": {
    "encryption": {
      "at_rest": {
        "enabled": true,
        "algorithm": "AES-256",
        "key_management": "aws-kms"
      }
    }
  }
}
```

#### Field-Level Encryption

**Status**: Planned  
**Priority**: Medium  
**Description**: Encrypt specific fields within documents.

**Features**:
- Selective field encryption
- Different encryption keys per field
- Transparent encryption/decryption
- Key rotation support

**Configuration**:
```json
{
  "security": {
    "encryption": {
      "field_level": {
        "enabled": true,
        "encrypted_fields": ["ssn", "credit_card", "password"]
      }
    }
  }
}
```

#### Data Masking

**Status**: Planned  
**Priority**: Medium  
**Description**: Mask sensitive data in query results.

**Features**:
- Configurable masking rules
- Partial masking (e.g., show last 4 digits)
- Role-based masking
- Audit trail for masked access

**Configuration**:
```json
{
  "security": {
    "data_masking": {
      "enabled": true,
      "rules": {
        "ssn": "mask_all",
        "credit_card": "show_last_4",
        "email": "mask_domain"
      }
    }
  }
}
```

### Phase 5: Compliance Features

#### GDPR Compliance Tools

**Status**: Planned  
**Priority**: High  
**Description**: Tools to support GDPR compliance.

**Features**:
- **Right to Deletion**: Delete all user data
- **Right to Access**: Export user data
- **Right to Portability**: Data export in standard formats
- **Consent Management**: Track and manage user consent

**Implementation**:
```python
# Right to deletion
await engine.delete_user_data("user_id", app_slug="my_app")

# Right to access
user_data = await engine.export_user_data("user_id", app_slug="my_app")

# Right to portability
export_file = await engine.export_user_data_portable(
    "user_id",
    app_slug="my_app",
    format="json"
)
```

#### Audit Logging Enhancement

**Status**: Planned  
**Priority**: Medium  
**Description**: Enhanced audit logging with structured events.

**Features**:
- Structured audit events
- Event correlation
- Long-term storage
- Compliance reporting

**Event Schema**:
```json
{
  "timestamp": "2024-01-01T12:00:00Z",
  "event_type": "query_execution",
  "app_slug": "my_app",
  "user_id": "user123",
  "action": "find",
  "collection": "users",
  "filter": {"status": "active"},
  "result": "success",
  "duration_ms": 45,
  "security_checks": {
    "query_validation": "passed",
    "resource_limits": "passed"
  }
}
```

#### Compliance Reporting

**Status**: Planned  
**Priority**: Medium  
**Description**: Automated compliance reporting.

**Features**:
- SOC 2 reports
- GDPR compliance reports
- HIPAA compliance reports
- Custom compliance reports

### Phase 6: Threat Detection

#### Anomaly Detection

**Status**: Planned  
**Priority**: Medium  
**Description**: Detect unusual access patterns and potential threats.

**Features**:
- Machine learning-based detection
- Pattern recognition
- Real-time alerts
- Threat scoring

**Detection Types**:
- Unusual query patterns
- Unusual access times
- Unusual data access volumes
- Unusual cross-app access

#### Intrusion Detection

**Status**: Planned  
**Priority**: High  
**Description**: Detect and respond to intrusion attempts.

**Features**:
- Attack pattern detection
- Automatic blocking
- Alert generation
- Incident response integration

**Attack Types Detected**:
- SQL/NoSQL injection attempts
- Brute force attacks
- Privilege escalation attempts
- Data exfiltration attempts

#### Security Analytics

**Status**: Planned  
**Priority**: Low  
**Description**: Advanced security analytics and reporting.

**Features**:
- Security dashboards
- Trend analysis
- Risk scoring
- Predictive analytics

### Phase 7: Secrets Management

#### Secure Credential Storage

**Status**: Planned  
**Priority**: High  
**Description**: Secure storage and management of credentials.

**Features**:
- Encrypted credential storage
- Key rotation
- Access control
- Audit logging

**Integration**:
- AWS Secrets Manager
- HashiCorp Vault
- Azure Key Vault
- Google Secret Manager

#### Credential Rotation

**Status**: Planned  
**Priority**: Medium  
**Description**: Automatic credential rotation.

**Features**:
- Scheduled rotation
- Zero-downtime rotation
- Rotation policies
- Rotation notifications

### Phase 8: Performance and Scalability

#### Security Performance Optimization

**Status**: Planned  
**Priority**: Medium  
**Description**: Optimize security features for performance.

**Features**:
- Caching of validation results
- Parallel validation
- Lazy validation
- Performance profiling

#### Distributed Security

**Status**: Planned  
**Priority**: Low  
**Description**: Security features for distributed deployments.

**Features**:
- Distributed rate limiting
- Distributed caching
- Cross-node security
- Load balancing integration

### Implementation Timeline

| Phase | Timeline | Priority |
|-------|----------|----------|
| Phase 1: Enhanced Query Security | Q2 2024 | High |
| Phase 2: Network Security | Q3 2024 | High |
| Phase 3: Advanced Access Control | Q4 2024 | Medium |
| Phase 4: Data Protection | Q1 2025 | High |
| Phase 5: Compliance Features | Q2 2025 | High |
| Phase 6: Threat Detection | Q3 2025 | Medium |
| Phase 7: Secrets Management | Q4 2025 | High |
| Phase 8: Performance Optimization | Ongoing | Medium |

### Contributing

If you're interested in contributing to security enhancements, please:

1. Review the [Contributing Guide](../CONTRIBUTING.md)
2. Check existing issues and pull requests
3. Discuss your proposal in an issue before starting work
4. Follow security best practices in your implementation
5. Include comprehensive tests and documentation

## Conclusion

The MongoDB Engine provides comprehensive security features designed to protect your applications and data. By following the best practices outlined in this guide and staying informed about security updates, you can ensure your applications remain secure.

For questions or security concerns, please:
- Open an issue on GitHub
- Contact the security team
- Review the [Security Policy](../SECURITY.md)

## Appendix A: Security Testing

### Testing Philosophy

The MongoDB Engine uses a comprehensive testing strategy to ensure security features work correctly:

- **Defense in Depth Testing**: Each security layer is tested independently and together
- **Unit Tests**: Fast, isolated tests for individual security components
- **Integration Tests**: End-to-end tests with real MongoDB instances
- **Coverage Goals**: 70%+ overall coverage, 90%+ for critical security paths

### What We Test

#### Query Security
- ✅ Dangerous operator blocking (`$where`, `$eval`, `$function`, `$accumulator`)
- ✅ Query complexity limits (nesting depth, pipeline stages, sort fields)
- ✅ Regex validation (length, complexity, ReDoS prevention)
- ✅ Aggregation pipeline validation

#### Resource Limits
- ✅ Query timeout enforcement (default and maximum limits)
- ✅ Result size limits (capping, not rejection)
- ✅ Document size validation (16MB MongoDB limit)
- ✅ Batch size limits

#### Collection Name Security
- ✅ Format validation (alphanumeric, underscores, dots, hyphens)
- ✅ Reserved name blocking (`system*`, `admin*`, `config*`, `local*`)
- ✅ Path traversal protection (`../`, `/`, `\`)
- ✅ Length validation (1-255 characters)
- ✅ Start character validation (must start with letter or underscore)

#### Cross-App Access Control
- ✅ Authorized cross-app access (apps in `read_scopes`)
- ✅ Unauthorized access blocking (apps not in `read_scopes`)
- ✅ Multi-word app slug handling
- ✅ Access logging (authorized and unauthorized attempts)

#### Data Isolation
- ✅ Automatic `app_id` filtering on read operations
- ✅ Automatic `app_id` injection on write operations
- ✅ Multi-scope read operations
- ✅ Scope validation

### How We Test

#### Unit Tests

Located in `tests/unit/`, these tests use mocks and don't require MongoDB:

**`test_query_validator.py`**
- Tests dangerous operator detection
- Tests query complexity limits
- Tests regex validation
- Tests aggregation pipeline validation

**`test_resource_limiter.py`**
- Tests timeout enforcement
- Tests result size limits
- Tests document size validation
- Tests limit capping behavior

**`test_scoped_wrapper.py`**
- Tests collection name validation
- Tests cross-app access control
- Tests reserved name blocking
- Tests path traversal protection

**`test_scoped_wrapper_security_integration.py`**
- Tests security features working together
- Tests validator and limiter integration
- Tests error handling

#### Integration Tests

Located in `tests/integration/`, these tests use real MongoDB instances:

**`test_scoped_isolation.py`**
- Tests data isolation between apps
- Tests multi-scope read operations
- Tests cross-app access with real MongoDB

### Test Examples

#### Example: Testing Dangerous Operator Blocking

```python
def test_dangerous_operator_blocked():
    """Test that dangerous operators are blocked."""
    validator = QueryValidator()
    
    # Should raise QueryValidationError
    with pytest.raises(QueryValidationError, match="Dangerous operator"):
        validator.validate_filter({"$where": "true"})
```

#### Example: Testing Collection Name Validation

```python
def test_invalid_collection_name_format():
    """Test that invalid collection names are rejected."""
    wrapper = ScopedMongoWrapper(
        real_db=mock_mongo_database,
        read_scopes=["test_app"],
        write_scope="test_app",
    )
    
    # Should raise ValueError
    with pytest.raises(ValueError):
        _ = getattr(wrapper, "123invalid")  # Starts with number
```

#### Example: Testing Data Isolation

```python
async def test_data_isolation_between_apps(real_mongodb_engine):
    """Test that apps have isolated data."""
    engine = real_mongodb_engine
    
    # Register two apps
    await engine.register_app(app1_manifest)
    await engine.register_app(app2_manifest)
    
    # Insert data into same collection name
    db1 = engine.get_scoped_db("app1")
    db2 = engine.get_scoped_db("app2")
    
    await db1.test_collection.insert_one({"name": "App 1"})
    await db2.test_collection.insert_one({"name": "App 2"})
    
    # Verify isolation
    app1_docs = await db1.test_collection.find({}).to_list(length=100)
    assert len(app1_docs) == 1
    assert app1_docs[0]["name"] == "App 1"
    # app1 should not see app2's data
```

### Running Security Tests

#### Run All Security Tests

```bash
# Run all tests (includes security tests)
make test-coverage

# Run only unit tests (fast, no MongoDB)
make test-unit

# Run only integration tests (requires MongoDB)
make test-integration
```

#### Run Specific Security Test Suites

```bash
# Query validation tests
pytest tests/unit/test_query_validator.py -v

# Resource limiter tests
pytest tests/unit/test_resource_limiter.py -v

# Scoped wrapper security tests
pytest tests/unit/test_scoped_wrapper.py::TestScopedMongoWrapperSecurity -v

# Security integration tests
pytest tests/unit/test_scoped_wrapper_security_integration.py -v

# Data isolation integration tests
pytest tests/integration/test_scoped_isolation.py -v
```

#### Test Coverage for Security

```bash
# Generate coverage report
make test-coverage

# Generate HTML coverage report
make test-coverage-html
# Open htmlcov/index.html in browser

# Check coverage for specific security modules
pytest --cov=mdb_engine/database/query_validator \
       --cov=mdb_engine/database/resource_limiter \
       --cov=mdb_engine/database/scoped_wrapper \
       --cov-report=html
```

### Writing New Security Tests

When adding new security features, follow these guidelines:

1. **Add Unit Tests**: Test the security feature in isolation
2. **Add Integration Tests**: Test the feature with real MongoDB
3. **Test Both Positive and Negative Cases**: Test that valid operations work and invalid operations are blocked
4. **Test Error Messages**: Ensure error messages are clear and helpful
5. **Test Edge Cases**: Test boundary conditions and unusual inputs
6. **Maintain Coverage**: Ensure new tests maintain or improve coverage

#### Test Template

```python
import pytest
from mdb_engine.exceptions import QueryValidationError

class TestNewSecurityFeature:
    """Test new security feature."""
    
    def test_feature_blocks_invalid_input(self):
        """Test that invalid input is blocked."""
        # Setup
        validator = QueryValidator()
        
        # Test
        with pytest.raises(QueryValidationError):
            validator.validate_filter(invalid_input)
    
    def test_feature_allows_valid_input(self):
        """Test that valid input is allowed."""
        # Setup
        validator = QueryValidator()
        
        # Test
        validator.validate_filter(valid_input)  # Should not raise
```

### Continuous Integration

Security tests are automatically run in CI/CD pipelines:

- All tests must pass before merging PRs
- Coverage must meet minimum thresholds (70%+)
- Integration tests run against real MongoDB instances
- Security tests are prioritized in test execution

### Security Test Maintenance

- **Regular Review**: Security tests are reviewed quarterly
- **Update Tests**: Tests are updated when security features change
- **Coverage Monitoring**: Coverage is monitored and improved over time
- **Bug Reports**: Security bugs trigger new test cases

---

**Last Updated**: 2025-12-28  
**Version**: 1.0.0  
**Test Coverage**: 75%+ (as of 2025-12-28)  
**Maintainer**: MongoDB Engine Team
