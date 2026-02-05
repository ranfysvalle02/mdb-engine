#!/bin/bash
# ==============================================================================
# Docker Entrypoint for SSO Multi-App with CSFLE Support
# ==============================================================================
#
# This entrypoint script handles:
# 1. Auto-generating CSFLE local master key on first run
# 2. Persisting the key in a Docker volume for subsequent runs
# 3. Running the specified command
#
# ==============================================================================

set -e

# Key file location (should be in a mounted volume for persistence)
KEY_FILE="${CSFLE_KEY_FILE:-/data/csfle/.local_master_key}"
KEY_DIR=$(dirname "$KEY_FILE")

# Function to generate a CSFLE local master key
generate_key() {
    python3 -c "
import secrets
import base64
key = secrets.token_bytes(96)
print(base64.b64encode(key).decode())
"
}

# Only handle CSFLE key for apps that need it (ai-chat/sso-app-3)
if [ "${CSFLE_ENABLED:-false}" = "true" ]; then
    echo "=== CSFLE Key Management ===" >&2

    # Check if key is already set via environment
    if [ -n "$MDB_CSFLE_LOCAL_KEY" ]; then
        echo "Using CSFLE key from environment variable" >&2
    else
        # Ensure key directory exists
        mkdir -p "$KEY_DIR" 2>/dev/null || true

        # Check if key file exists
        if [ -f "$KEY_FILE" ]; then
            echo "Loading CSFLE key from persistent storage: $KEY_FILE" >&2
            export MDB_CSFLE_LOCAL_KEY=$(cat "$KEY_FILE")
        else
            # Generate new key
            echo "Generating new CSFLE local master key..." >&2
            NEW_KEY=$(generate_key)
            
            # Try to persist to file (may fail if volume not writable)
            if echo "$NEW_KEY" > "$KEY_FILE" 2>/dev/null; then
                chmod 600 "$KEY_FILE"
                echo "CSFLE key generated and persisted to: $KEY_FILE" >&2
                echo "Key will persist across container restarts." >&2
            else
                echo "WARNING: Could not persist CSFLE key to $KEY_FILE" >&2
                echo "Key will be lost on container restart!" >&2
                echo "Mount a volume to /data/csfle for persistent encryption." >&2
            fi
            
            export MDB_CSFLE_LOCAL_KEY="$NEW_KEY"
        fi
    fi

    # Verify key is set
    if [ -n "$MDB_CSFLE_LOCAL_KEY" ]; then
        KEY_LEN=${#MDB_CSFLE_LOCAL_KEY}
        echo "CSFLE key configured (length: $KEY_LEN chars)" >&2
    else
        echo "ERROR: CSFLE key not configured!" >&2
    fi

    echo "=== CSFLE Ready ===" >&2
fi

# Execute the main command
exec "$@"
