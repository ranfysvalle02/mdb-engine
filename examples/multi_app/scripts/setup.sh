#!/bin/bash
# ============================================================================
# Multi-App Example Setup Script
# Generates master key and sets up environment
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🔐 Multi-App Example Setup"
echo "=========================="
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is required but not found"
    exit 1
fi

# Generate master key
echo "Generating master key..."
MASTER_KEY=$(python3 -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())')

if [ -z "$MASTER_KEY" ]; then
    echo "❌ Error: Failed to generate master key"
    exit 1
fi

echo "✅ Master key generated"
echo ""

# Create .env if it doesn't exist
ENV_FILE="$EXAMPLE_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    # Create basic .env file with master key
    cat > "$ENV_FILE" << EOF
# Master Key for Envelope Encryption
MDB_ENGINE_MASTER_KEY=$MASTER_KEY

# App Secrets (generated at registration, retrieve from logs)
CLICK_TRACKER_SECRET=
DASHBOARD_SECRET=

# MongoDB Configuration (defaults work with docker-compose)
MONGODB_URI=mongodb://admin:password@mongodb:27017/?authSource=admin
MONGODB_DB=mdb_runtime
EOF
    echo "✅ Created .env file"
fi

# Add or update master key in .env
if grep -q "^MDB_ENGINE_MASTER_KEY=" "$ENV_FILE"; then
    # Update existing master key
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s|^MDB_ENGINE_MASTER_KEY=.*|MDB_ENGINE_MASTER_KEY=$MASTER_KEY|" "$ENV_FILE"
    else
        # Linux
        sed -i "s|^MDB_ENGINE_MASTER_KEY=.*|MDB_ENGINE_MASTER_KEY=$MASTER_KEY|" "$ENV_FILE"
    fi
    echo "✅ Updated master key in .env"
else
    # Add new master key
    echo "MDB_ENGINE_MASTER_KEY=$MASTER_KEY" >> "$ENV_FILE"
    echo "✅ Added master key to .env"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "1. Review .env file: $ENV_FILE"
echo "2. Start services: docker-compose up"
echo "3. Check logs for generated app secrets:"
echo "   docker-compose logs click-tracker | grep 'Generated secret'"
echo "   docker-compose logs dashboard | grep 'Generated secret'"
echo "4. Add secrets to .env and restart:"
echo "   docker-compose restart click-tracker dashboard"
echo ""

