# App-Level Authentication Guide

Comprehensive guide to app-level authentication using envelope encryption in MDB_ENGINE.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [How It Works](#how-it-works)
3. [Master Key Management](#master-key-management)
4. [Secret Lifecycle](#secret-lifecycle)
5. [Cross-App Access](#cross-app-access)
6. [Security Best Practices](#security-best-practices)
7. [Troubleshooting](#troubleshooting)
8. [API Reference](#api-reference)
9. [Migration Guide](#migration-guide)

## Architecture Overview

### Envelope Encryption Model

MDB_ENGINE uses **envelope encryption** to securely store app secrets:

```
┌─────────────────────────────────────────┐
│     Master Key (MK)                     │
│     From: MDB_ENGINE_MASTER_KEY         │
│     Encrypts: DEKs                      │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│     Data Encryption Key (DEK)            │
│     Per-app, encrypted with MK           │
│     Encrypts: App Secrets                │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│     App Secret (Plaintext)               │
│     Auto-generated at registration        │
└─────────────────────────────────────────┘
```

### Component Relationships

```
┌──────────────┐
│ Application  │
│   (App A)    │
└──────┬───────┘
       │ Provides app_token
       ▼
┌──────────────────────────────────┐
│      MongoDBEngine               │
│  • Verifies app_token            │
│  • Validates read_scopes         │
└──────┬───────────────────────────┘
       │
       ├──► AppSecretsManager
       │    • Stores encrypted secrets
       │    • Verifies tokens
       │
       └──► EnvelopeEncryptionService
            • Encrypts/decrypts secrets
            • Manages master key
```

### Security Boundaries

- **`_mdb_engine_app_secrets` collection**: Only accessible via raw MongoDB client (not scoped wrapper)
- **Master Key**: Stored in environment variable or key management service
- **App Secrets**: Encrypted at rest, never logged
- **Token Verification**: Constant-time comparison to prevent timing attacks

## How It Works

### Step 1: App Registration

When you register an app, the engine:

1. **Generates app secret**: Creates a random 256-bit secret using `secrets.token_urlsafe(32)`
2. **Encrypts secret**: Uses envelope encryption (DEK encrypted with master key)
3. **Stores encrypted secret**: Saves to `_mdb_engine_app_secrets` collection
4. **Extracts data_access**: Reads `read_scopes` from manifest
5. **Stores read_scopes**: Maps app_slug → authorized read_scopes

```python
# Register app
manifest = {
    "slug": "my_app",
    "data_access": {
        "read_scopes": ["my_app", "shared_app"],
    },
}

await engine.register_app(manifest)
# Secret is generated and stored encrypted
```

### Step 2: Runtime Verification

When your app requests database access:

1. **App provides token**: Passes `app_token` to `get_scoped_db()`
2. **Engine retrieves encrypted secret**: Reads from `_mdb_engine_app_secrets`
3. **Engine decrypts secret**: Uses master key to decrypt DEK, then DEK to decrypt secret
4. **Engine compares tokens**: Constant-time comparison of provided token vs stored secret
5. **Engine validates scopes**: Checks requested `read_scopes` against manifest authorization
6. **Engine returns scoped DB**: If all checks pass, returns `ScopedMongoWrapper`

```python
# Get scoped database with token
db = engine.get_scoped_db(
    "my_app",
    app_token=os.getenv("MY_APP_SECRET"),  # From environment
    read_scopes=["my_app", "shared_app"],  # Optional - uses manifest if not provided
)
```

### Step 3: Cross-App Access

When accessing another app's collections:

1. **Collection name extraction**: Engine extracts app slug from collection name (e.g., `shared_app_data` → `shared_app`)
2. **Scope validation**: Checks if target app is in `read_scopes`
3. **Access granted/denied**: Allows or blocks based on authorization
4. **Audit logging**: Logs all cross-app access attempts

```python
# Access shared app's collection
shared_data = await db.get_collection("shared_app_data").find({})
# Engine validates: Is "shared_app" in read_scopes? ✅ Yes → Allow
```

## Master Key Management

### Development Setup

**Generate Master Key:**

```bash
python -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'
```

**Store in `.env`:**

```bash
MDB_ENGINE_MASTER_KEY="<generated-key-here>"
```

**Important**: Add `.env` to `.gitignore` - never commit master keys!

### Production Setup

**Option 1: Environment Variable**

```bash
# Set in your deployment environment
export MDB_ENGINE_MASTER_KEY="<base64-encoded-32-byte-key>"
```

**Option 2: Key Management Service (Recommended)**

Integrate with AWS KMS, HashiCorp Vault, or Azure Key Vault:

```python
# Example: AWS KMS integration
import boto3

kms = boto3.client('kms')
response = kms.decrypt(CiphertextBlob=encrypted_master_key)
master_key = response['Plaintext']

service = EnvelopeEncryptionService(master_key=master_key)
```

### Key Rotation Procedures

**Rotating Master Key:**

1. Generate new master key
2. Re-encrypt all DEKs with new master key
3. Update `MDB_ENGINE_MASTER_KEY` environment variable
4. Restart engine

**Note**: Master key rotation requires re-encrypting all stored DEKs. This is a planned feature.

### Best Practices

- **Never commit master keys**: Use `.gitignore` for `.env` files
- **Use key management services**: In production, use KMS/Vault
- **Rotate regularly**: Rotate master keys annually or per security policy
- **Backup securely**: Store master key backups in secure location
- **Limit access**: Only authorized personnel should have master key access

## Secret Lifecycle

### Generation at Registration

Secrets are automatically generated when you register an app:

```python
await engine.register_app(manifest)
# Secret generated: "xK9mP2qR7sT4uV6wY8zA1bC3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA"
# Stored encrypted in: _mdb_engine_app_secrets collection
```

**Secret Format**: Base64-encoded URL-safe string (256 bits = 32 bytes)

### Storage in `_mdb_engine_app_secrets`

Secrets are stored encrypted:

```json
{
  "_id": "my_app",
  "encrypted_secret": "<base64-encoded-encrypted-secret>",
  "encrypted_dek": "<base64-encoded-encrypted-dek>",
  "algorithm": "AES-256-GCM",
  "created_at": ISODate("2024-01-01T12:00:00Z"),
  "updated_at": ISODate("2024-01-01T12:00:00Z"),
  "rotation_count": 0
}
```

**Access Control**: This collection is **never** accessible via scoped wrapper. Only `MongoDBEngine` and `AppSecretsManager` can access it.

### Verification at Runtime

When `get_scoped_db()` is called:

1. Engine reads encrypted secret from database
2. Decrypts DEK with master key
3. Decrypts secret with DEK
4. Compares provided token with decrypted secret (constant-time)
5. Returns scoped database if match, raises error if mismatch

**Constant-Time Comparison**: Uses `secrets.compare_digest()` to prevent timing attacks.

### Rotation Procedures

**Rotate App Secret:**

```python
# Generate new secret, update storage
new_secret = await engine._app_secrets_manager.rotate_app_secret("my_app")

# Update your environment variable
# export MY_APP_SECRET="new-secret"

# Restart your application
```

**Zero-Downtime Rotation:**

1. Generate new secret
2. Store new encrypted secret (old one remains until rotation complete)
3. Update application code to use new secret
4. Deploy application
5. Old secret becomes invalid automatically

## Cross-App Access

### Manifest-Level Configuration

Declare cross-app access in your manifest:

```json
{
  "slug": "my_app",
  "data_access": {
    "read_scopes": ["my_app", "shared_app", "analytics_app"],
    "write_scope": "my_app",
    "cross_app_policy": "explicit"
  }
}
```

**Configuration Options:**

- **`read_scopes`**: List of app slugs this app can read from
- **`write_scope`**: App slug this app writes to (always own app)
- **`cross_app_policy`**: `"explicit"` (default) or `"deny_all"`

### Runtime Scope Validation

When you request `read_scopes`:

```python
# Requested scopes are validated against manifest authorization
db = engine.get_scoped_db(
    "my_app",
    app_token="secret",
    read_scopes=["my_app", "shared_app"],  # Must be subset of manifest read_scopes
)
```

**Validation Rules:**

- Requested `read_scopes` must be subset of manifest `read_scopes`
- Unauthorized scopes raise `ValueError`
- If `read_scopes` not provided, uses manifest `read_scopes`

### Accessing Cross-App Collections

Use fully prefixed collection names:

```python
# Access shared_app's collection
shared_data = await db.get_collection("shared_app_data").find({})

# Engine automatically:
# 1. Extracts "shared_app" from collection name
# 2. Validates "shared_app" is in read_scopes
# 3. Includes "shared_app" in app_id filter
# 4. Logs cross-app access
```

### Examples

**Authorized Cross-App Access:**

```python
# Manifest declares: read_scopes: ["my_app", "shared_app"]
db = engine.get_scoped_db("my_app", app_token="secret")

# ✅ This works (authorized)
data = await db.get_collection("shared_app_users").find({})
```

**Unauthorized Cross-App Access:**

```python
# Manifest declares: read_scopes: ["my_app"] (no "other_app")
db = engine.get_scoped_db("my_app", app_token="secret")

# ❌ This fails (unauthorized)
data = await db.get_collection("other_app_data").find({})
# Raises: ValueError("Access to collection 'other_app_data' not authorized")
```

## Security Best Practices

### Never Log Secrets

**❌ Bad:**
```python
logger.info(f"App secret: {secret}")  # NEVER DO THIS
logger.debug(f"Encrypted secret: {encrypted}")  # Also bad
```

**✅ Good:**
```python
logger.info(f"Secret stored for app '{app_slug}'")  # OK - no secret value
logger.debug(f"Secret verification {'succeeded' if result else 'failed'}")  # OK
```

### Secure Master Key Storage

**Development:**
- Store in `.env` file (gitignored)
- Never commit to version control
- Use different keys per environment

**Production:**
- Use key management service (AWS KMS, HashiCorp Vault)
- Rotate master keys regularly
- Limit access to authorized personnel
- Monitor key access logs

### Regular Secret Rotation

**Rotation Schedule:**
- **App secrets**: Quarterly (or per security policy)
- **Master key**: Annually (or per compliance requirements)

**Rotation Process:**
1. Generate new secret/key
2. Update storage (encrypted)
3. Update application configuration
4. Deploy application
5. Verify access works
6. Monitor for issues

### Audit Trail Usage

All security events are logged:

- **Secret verification attempts**: Success/failure
- **Cross-app access**: Authorized/unauthorized
- **Token validation failures**: Invalid tokens
- **Scope validation failures**: Unauthorized scopes

**Monitor Logs For:**
- Repeated failed verification attempts (potential attack)
- Unauthorized cross-app access attempts
- Unusual access patterns
- Token validation failures

### Defense-in-Depth Strategies

1. **Envelope Encryption**: Secrets encrypted at rest
2. **Manifest-Level Authorization**: Declarative access control
3. **Runtime Verification**: Token verification on every access
4. **Scope Validation**: Cross-app access validated
5. **OSO Integration**: Fine-grained authorization (optional)
6. **Audit Logging**: All access attempts logged

## Troubleshooting

### Invalid Token Errors

**Error**: `ValueError: Invalid app token for 'my_app'`

**Diagnosis:**
1. Check if secret matches stored value
2. Verify secret wasn't rotated
3. Check master key is correct
4. Verify environment variable is set

**Resolution:**
```bash
# Retrieve current secret (if you have access)
python -c "
import asyncio
from mdb_engine import MongoDBEngine
from mdb_engine.core.encryption import EnvelopeEncryptionService, MASTER_KEY_ENV_VAR
import os

async def get_secret():
    engine = MongoDBEngine('mongodb://localhost:27017', 'test_db')
    await engine.initialize()
    secret = await engine._app_secrets_manager.get_app_secret('my_app')
    print(secret)

asyncio.run(get_secret())
"

# Update environment variable
export MY_APP_SECRET="<retrieved-secret>"
```

### Master Key Not Found

**Error**: `ValueError: Master key not found`

**Diagnosis:**
- `MDB_ENGINE_MASTER_KEY` environment variable not set
- Master key format invalid

**Resolution:**
```bash
# Generate master key
python -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'

# Set environment variable
export MDB_ENGINE_MASTER_KEY="<generated-key>"
```

### Cross-App Access Denied

**Error**: `ValueError: App 'my_app' not authorized to read from 'other_app'`

**Diagnosis:**
1. Check manifest `data_access.read_scopes`
2. Verify `other_app` is in `read_scopes`
3. Check if manifest was loaded correctly

**Resolution:**
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

### Common Configuration Mistakes

**Mistake 1**: Forgetting to set `app_token`
```python
# ❌ Missing app_token
db = engine.get_scoped_db("my_app")

# ✅ Provide app_token
db = engine.get_scoped_db("my_app", app_token=os.getenv("MY_APP_SECRET"))
```

**Mistake 2**: Wrong collection name format
```python
# ❌ Missing app prefix
data = await db.shared_app_data.find({})  # Treated as my_app_shared_app_data

# ✅ Use get_collection with full prefix
data = await db.get_collection("shared_app_data").find({})
```

**Mistake 3**: Not including own app in read_scopes
```json
// ❌ Missing own app
{
  "read_scopes": ["shared_app"]  // Can't read own data!
}

// ✅ Include own app
{
  "read_scopes": ["my_app", "shared_app"]
}
```

## API Reference

### `register_app(manifest: ManifestDict) -> bool`

Register an app and generate encrypted secret.

**Parameters:**
- `manifest`: App manifest dictionary (must include `slug` and optionally `data_access`)

**Returns:**
- `True` if registration successful

**Side Effects:**
- Generates app secret (if not exists)
- Stores encrypted secret in `_mdb_engine_app_secrets`
- Extracts and stores `data_access.read_scopes` mapping

**Example:**
```python
manifest = {
    "slug": "my_app",
    "data_access": {
        "read_scopes": ["my_app", "shared_app"],
    },
}
await engine.register_app(manifest)
```

### `get_scoped_db(app_slug: str, app_token: str, read_scopes: Optional[List[str]] = None, write_scope: Optional[str] = None) -> ScopedMongoWrapper`

Get scoped database with app token verification.

**Parameters:**
- `app_slug`: App slug identifier
- `app_token`: App secret token (required if app has stored secret)
- `read_scopes`: Optional list of app slugs to read from (uses manifest if not provided)
- `write_scope`: Optional app slug to write to (defaults to `app_slug`)

**Returns:**
- `ScopedMongoWrapper` instance

**Raises:**
- `ValueError`: If app_token is invalid or read_scopes are unauthorized
- `RuntimeError`: If engine not initialized

**Example:**
```python
db = engine.get_scoped_db(
    "my_app",
    app_token=os.getenv("MY_APP_SECRET"),
    read_scopes=["my_app", "shared_app"],
)
```

### `AppSecretsManager.store_app_secret(app_slug: str, secret: str) -> None`

Store encrypted app secret.

**Parameters:**
- `app_slug`: App slug identifier
- `secret`: Plaintext secret to encrypt and store

**Raises:**
- `OperationFailure`: If MongoDB operation fails

### `AppSecretsManager.verify_app_secret(app_slug: str, provided_secret: str) -> bool`

Verify app secret against stored encrypted value.

**Parameters:**
- `app_slug`: App slug identifier
- `provided_secret`: Secret to verify

**Returns:**
- `True` if secret matches, `False` otherwise

### `AppSecretsManager.rotate_app_secret(app_slug: str) -> str`

Rotate app secret (generate new secret, re-encrypt and store).

**Parameters:**
- `app_slug`: App slug identifier

**Returns:**
- New plaintext secret (caller must store securely)

### `AppSecretsManager.get_app_secret(app_slug: str) -> Optional[str]`

Get decrypted app secret (for rotation purposes only).

**Warning**: Returns plaintext secret. Use only for rotation.

**Parameters:**
- `app_slug`: App slug identifier

**Returns:**
- Decrypted secret if found, `None` otherwise

### `EnvelopeEncryptionService.generate_master_key() -> str`

Generate a new master key.

**Returns:**
- Base64-encoded master key string (suitable for environment variable)

**Example:**
```python
key = EnvelopeEncryptionService.generate_master_key()
# Store in .env: MDB_ENGINE_MASTER_KEY={key}
```

### `EnvelopeEncryptionService.encrypt_secret(secret: str, master_key: Optional[bytes] = None) -> Tuple[bytes, bytes]`

Encrypt a secret using envelope encryption.

**Parameters:**
- `secret`: Plaintext secret to encrypt
- `master_key`: Optional master key (uses instance key if not provided)

**Returns:**
- Tuple of `(encrypted_secret, encrypted_dek)` as bytes

### `EnvelopeEncryptionService.decrypt_secret(encrypted_secret: bytes, encrypted_dek: bytes, master_key: Optional[bytes] = None) -> str`

Decrypt a secret using envelope decryption.

**Parameters:**
- `encrypted_secret`: Encrypted secret (with nonce prepended)
- `encrypted_dek`: Encrypted DEK (with nonce prepended)
- `master_key`: Optional master key (uses instance key if not provided)

**Returns:**
- Decrypted plaintext secret

**Raises:**
- `ValueError`: If decryption fails (invalid key, corrupted data, etc.)

## Migration Guide

### Upgrading Existing Apps

**Step 1**: Update Engine

```bash
pip install --upgrade mdb-engine
```

**Step 2**: Set Master Key

```bash
# Generate master key
python -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'

# Set environment variable
export MDB_ENGINE_MASTER_KEY="<generated-key>"
```

**Step 3**: Re-register Apps

When you next call `register_app()`, secrets will be automatically generated:

```python
# Existing registration code
await engine.register_app(manifest)
# Secret is now generated and stored automatically
```

**Step 4**: Update Application Code

Add `app_token` parameter to `get_scoped_db()` calls:

```python
# Old code (still works for apps without secrets)
db = engine.get_scoped_db("my_app")

# New code (required for apps with secrets)
db = engine.get_scoped_db("my_app", app_token=os.getenv("MY_APP_SECRET"))
```

**Step 5**: Add Manifest `data_access` (Optional)

Declare cross-app access in manifest:

```json
{
  "slug": "my_app",
  "data_access": {
    "read_scopes": ["my_app", "shared_app"],
  }
}
```

### Backward Compatibility

- **Apps without secrets**: Continue to work (backward compatible)
- **Apps with secrets**: Must provide `app_token` (new requirement)
- **No `data_access` in manifest**: Defaults to `[app_slug]` (backward compatible)

### Step-by-Step Migration Process

1. **Generate master key** and set `MDB_ENGINE_MASTER_KEY`
2. **Re-register apps** (secrets generated automatically)
3. **Retrieve secrets** (from logs or rotation API)
4. **Store secrets** in environment variables or secrets manager
5. **Update code** to provide `app_token` to `get_scoped_db()`
6. **Deploy** updated application
7. **Verify** access works correctly
8. **Monitor** logs for any issues

## Related Documentation

- [Security Guide](SECURITY.md) - Overall security architecture
- [Authorization Guide](AUTHZ.md) - OSO/Casbin authorization
- [Quick Start Guide](QUICK_START.md) - Getting started with MDB_ENGINE

