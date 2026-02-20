# Client-Side Field Level Encryption (CSFLE) Setup Guide

This guide explains how to enable MongoDB Client-Side Field Level Encryption (CSFLE) in your mdb-engine applications.

## Overview

CSFLE automatically encrypts sensitive fields in your MongoDB documents before they leave your application. This provides:

- **Defense in Depth**: Data is encrypted even if the database is compromised
- **Compliance**: Helps meet GDPR, HIPAA, and other data protection requirements
- **Transparent Encryption**: Application code works normally; encryption/decryption is automatic

## Quick Start

### 1. Install the crypt_shared Library

Run the setup script:

```bash
./scripts/setup_csfle.sh --generate-key
```

This will:
- Download the MongoDB crypt_shared library for your platform
- Generate a local master key for development
- Print the environment variables to add to your `.env` file

### 2. Add Environment Variables

Add to your `.env` file:

```bash
CRYPT_SHARED_LIB_PATH=/path/to/lib/mongo_crypt_v1.so  # or .dylib on macOS
MDB_CSFLE_LOCAL_KEY=<generated-key>  # For persistent encryption
```

### 3. Enable Encryption in Your Manifest

**Simple Mode** - Just add one line:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true
  }
}
```

That's it! Memory content is now automatically encrypted.

## Configuration Options

### Simple Mode: Memory Encryption

Add `"encrypted": true` to your `memory_config`:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true,
    "provider": "cognitive",
    "collection_name": "user_memories"
  }
}
```

**Default encrypted fields**: `content`, `text`

**Fields that remain queryable**: `user_id`, `session_id`, `created_at`, `importance`, `embedding`, `category`

### Advanced Mode: Custom Field Encryption

For more control, add an `encryption` object:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true,
    "encryption": {
      "kms_provider": "local",
      "fields": ["content", "text", "metadata.pii"],
      "key_vault_namespace": "encryption.__keyVault"
    }
  }
}
```

### Encrypting Custom Collections

Use `encrypted_fields` (similar to `managed_indexes`) to encrypt any collection:

```json
{
  "encrypted_fields": {
    "payments": ["card_number", "cvv", "billing_address"],
    "health_records": ["diagnosis", "medications", "notes"],
    "user_profiles": ["ssn", "date_of_birth"]
  },
  "encryption_config": {
    "kms_provider": "local"
  }
}
```

### Combining Memory + Custom Encryption

You can use both:

```json
{
  "memory_config": {
    "enabled": true,
    "encrypted": true
  },
  "encrypted_fields": {
    "sensitive_data": ["secret_field", "private_info"]
  }
}
```

## KMS Providers

### Local (Development)

```json
{
  "encryption": {
    "kms_provider": "local"
  }
}
```

Environment variables:
```bash
MDB_CSFLE_LOCAL_KEY=<96-byte-base64-encoded-key>
```

Generate a key:
```python
from mdb_engine.core.csfle import generate_local_master_key
print(f"MDB_CSFLE_LOCAL_KEY={generate_local_master_key()}")
```

### AWS KMS (Production)

```json
{
  "encryption": {
    "kms_provider": "aws"
  }
}
```

Environment variables:
```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_KMS_KEY_ARN=arn:aws:kms:us-east-1:123456789:key/...
```

### Azure Key Vault (Production)

```json
{
  "encryption": {
    "kms_provider": "azure"
  }
}
```

Environment variables:
```bash
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_KEY_VAULT_URL=https://your-vault.vault.azure.net
```

### Google Cloud KMS (Production)

```json
{
  "encryption": {
    "kms_provider": "gcp"
  }
}
```

Environment variables:
```bash
GCP_EMAIL=service-account@project.iam.gserviceaccount.com
GCP_PRIVATE_KEY=<private-key>
```

## Deployment

### Docker (Zero-Config Auto-Generation)

For Docker deployments, you can use an entrypoint script to automatically generate and persist the local master key. This allows for a "zero-config" experience where encryption works out of the box.

1.  **Create an entrypoint script (`docker-entrypoint.sh`)**:

    ```bash
    #!/bin/bash
    set -e

    KEY_FILE="/data/csfle/.local_master_key"
    mkdir -p "$(dirname "$KEY_FILE")"

    # Generate key if not exists
    if [ ! -f "$KEY_FILE" ] && [ -z "$MDB_CSFLE_LOCAL_KEY" ]; then
        echo "Generating new CSFLE local master key..."
        # Generate 96 random bytes and base64 encode
        python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(96)).decode())" > "$KEY_FILE"
    fi

    # Load key if not set in env
    if [ -z "$MDB_CSFLE_LOCAL_KEY" ] && [ -f "$KEY_FILE" ]; then
        export MDB_CSFLE_LOCAL_KEY=$(cat "$KEY_FILE")
    fi

    exec "$@"
    ```

2.  **Update your `Dockerfile`**:

    ```dockerfile
    FROM python:3.11-slim

    # Install dependencies
    RUN apt-get update && apt-get install -y libssl-dev curl && rm -rf /var/lib/apt/lists/*

    # Download crypt_shared library
    RUN mkdir -p /opt/mongodb/lib && \
        curl -L -o /tmp/mongo_crypt.tgz \
        "https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-x86_64-enterprise-ubuntu2204-8.0.0.tgz" && \
        tar -xzf /tmp/mongo_crypt.tgz -C /tmp && \
        find /tmp -name "mongo_crypt_v1.so" -exec cp {} /opt/mongodb/lib/ \; && \
        rm -rf /tmp/mongo_crypt*

    ENV CRYPT_SHARED_LIB_PATH=/opt/mongodb/lib/mongo_crypt_v1.so

    # Setup entrypoint and persistence directory
    COPY docker-entrypoint.sh /usr/local/bin/
    RUN chmod +x /usr/local/bin/docker-entrypoint.sh
    RUN mkdir -p /data/csfle

    WORKDIR /app
    COPY . .
    RUN pip install -r requirements.txt

    ENTRYPOINT ["docker-entrypoint.sh"]
    CMD ["python", "main.py"]
    ```

3.  **Configure `docker-compose.yml`**:

    Mount a volume to persist the key across container restarts.

    ```yaml
    services:
      app:
        build: .
        volumes:
          - csfle_keys:/data/csfle
        # ... other config

    volumes:
      csfle_keys:
    ```

With this setup:
-   **First Run**: Key is generated and saved to the `csfle_keys` volume.
-   **Restart**: Key is loaded from the volume, keeping data readable.
-   **Manual Override**: Set `MDB_CSFLE_LOCAL_KEY` env var to use a specific key.

### Docker (Manual / Standard)

If you prefer to manage the key manually (e.g., via secrets management):

1.  Add the `crypt_shared` library to your Docker image (same as above).
2.  Set `MDB_CSFLE_LOCAL_KEY` environment variable in your container definition.

### Render (PaaS)

**Option 1: Docker Deploy (Recommended)**

Use the Dockerfile above.

**Option 2: Native Runtime**

Create `render_build.sh`:

```bash
#!/bin/bash
set -e

pip install -r requirements.txt

# Download crypt_shared
curl -L -o /tmp/mongo_crypt.tgz \
  "https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-x86_64-enterprise-ubuntu2204-8.0.0.tgz"
tar -xzf /tmp/mongo_crypt.tgz -C /tmp
mkdir -p ./lib
find /tmp -name "mongo_crypt_v1.so" -exec cp {} ./lib/ \;
```

Set environment variables in Render dashboard:
- `CRYPT_SHARED_LIB_PATH`: `./lib/mongo_crypt_v1.so`
- `MDB_CSFLE_LOCAL_KEY`: Your generated key

## Python API Usage

### Building CSFLE Config from Manifests

```python
from pathlib import Path
from mdb_engine import MongoDBEngine
from mdb_engine.core.engine import build_csfle_config_from_manifests

# Scan directory for manifests with encryption config
csfle_config = build_csfle_config_from_manifests(
    manifests_dir=Path("./apps")
)

# Create engine with CSFLE
engine = MongoDBEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="mydb",
    csfle_config=csfle_config,
)

await engine.initialize()
```

### Building Config from Single Manifest

```python
import json
from mdb_engine.core.engine import build_csfle_config_from_manifest

with open("manifest.json") as f:
    manifest = json.load(f)

csfle_config = build_csfle_config_from_manifest(manifest)

if csfle_config:
    engine = MongoDBEngine(..., csfle_config=csfle_config)
```

### Manual CSFLEConfig Creation

```python
from mdb_engine.core.csfle import CSFLEConfig

# Simple memory encryption
config = CSFLEConfig.from_memory_config(
    {"encrypted": True, "collection_name": "memories"},
    app_slug="my-app"
)

# Custom field encryption
config = CSFLEConfig.from_encrypted_fields(
    {"payments": ["card_number", "cvv"]},
    {"kms_provider": "aws"},
    app_slug="my-app"
)

# Combine configs
combined = memory_config.merge_with(custom_config)
```

## Checking CSFLE Status

```python
from mdb_engine.core.csfle import get_csfle_status, is_csfle_available

# Quick check
if is_csfle_available():
    print("CSFLE is available")

# Detailed status
status = get_csfle_status()
print(status)
# {
#     "available": True,
#     "pymongo_encryption": True,
#     "crypt_shared_path": "/path/to/lib/mongo_crypt_v1.so",
#     "crypt_shared_exists": True,
#     "local_key_configured": True
# }
```

## Troubleshooting

### "pymongo[encryption] not installed"

Install the encryption extras:

```bash
pip install 'pymongo[encryption]'
```

### "crypt_shared library not found"

1. Run the setup script: `./scripts/setup_csfle.sh`
2. Verify `CRYPT_SHARED_LIB_PATH` points to the correct file
3. Check file permissions (should be readable)

### "No MDB_CSFLE_LOCAL_KEY found"

If `MDB_CSFLE_LOCAL_KEY` is not set:

1.  **In Docker (with entrypoint)**: An ephemeral key will be auto-generated and persisted if you've configured the entrypoint and volume as described in the [Docker Deployment](#docker-zero-config-auto-generation) section.
2.  **In Development (without entrypoint)**: An ephemeral key is generated in memory. **Warning**: Data encrypted with this key will be **unreadable after you restart the application**.
3.  **In Production**: You should always provide a persistent key (via `MDB_CSFLE_LOCAL_KEY` or a cloud KMS provider).

To generate a persistent key for manual use:
```bash
python -c "from mdb_engine.core.csfle import generate_local_master_key; print(f'MDB_CSFLE_LOCAL_KEY={generate_local_master_key()}')"
```

### "KMS credentials missing"

Ensure all required environment variables are set for your KMS provider. See the [KMS Providers](#kms-providers) section.

### Encrypted fields not queryable

CSFLE encrypts fields so they cannot be searched/queried. This is by design.

**Do NOT encrypt**:
- Fields used in queries (`user_id`, `session_id`, etc.)
- Fields used for indexing
- Vector embeddings (needed for similarity search)
- Timestamps (needed for sorting/filtering)

**Do encrypt**:
- Content/text that contains PII
- Sensitive metadata
- Any field not needed for queries

## Security Best Practices

1. **Never commit keys**: Add `.env` to `.gitignore`
2. **Use cloud KMS in production**: Local keys are for development only
3. **Back up auto-generated keys**: If using the Docker auto-generation method, ensure you back up the key file from the volume. If the volume is lost, your data is irretrievable.
    ```bash
    docker exec <container_name> cat /data/csfle/.local_master_key > csfle_backup.key
    ```
4. **Rotate keys periodically**: Plan for key rotation
5. **Limit encrypted fields**: Only encrypt what's necessary
6. **Monitor access**: Use MongoDB audit logs

## Further Reading

- [MongoDB CSFLE Documentation](https://www.mongodb.com/docs/manual/core/csfle/)
- [PyMongo Encryption](https://pymongo.readthedocs.io/en/stable/examples/encryption.html)
- [mdb-engine Security Guide](../SECURITY.md)
