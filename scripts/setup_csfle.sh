#!/bin/bash
# ==============================================================================
# MongoDB CSFLE Setup Script
# ==============================================================================
#
# Downloads and configures the MongoDB crypt_shared library for Client-Side
# Field Level Encryption (CSFLE).
#
# Usage:
#   ./scripts/setup_csfle.sh [--generate-key]
#
# Options:
#   --generate-key    Also generate a local master key for development
#
# After running this script:
#   1. Add CRYPT_SHARED_LIB_PATH to your .env file (printed at end)
#   2. Optionally add MDB_CSFLE_LOCAL_KEY for persistent encryption
#
# ==============================================================================

set -e

# Configuration
MONGODB_VERSION="${MONGODB_CRYPT_VERSION:-8.0.0}"
LIB_DIR="${CSFLE_LIB_DIR:-./lib}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  MongoDB CSFLE Setup Script${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Detect platform and architecture
PLATFORM=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

echo -e "${YELLOW}Detected:${NC} $PLATFORM / $ARCH"

# Map architecture names
case "$ARCH" in
    x86_64)
        ARCH="x86_64"
        ;;
    arm64|aarch64)
        ARCH="aarch64"
        ;;
    *)
        echo -e "${RED}Error: Unsupported architecture: $ARCH${NC}"
        echo "Supported architectures: x86_64, arm64/aarch64"
        exit 1
        ;;
esac

# Build download URL based on platform
case "$PLATFORM" in
    darwin)
        # macOS
        DOWNLOAD_URL="https://downloads.mongodb.com/osx/mongo_crypt_shared_v1-macos-${ARCH}-enterprise-${MONGODB_VERSION}.tgz"
        LIB_NAME="mongo_crypt_v1.dylib"
        ;;
    linux)
        # Linux - default to Ubuntu 22.04 (Debian-based, works on most systems)
        DOWNLOAD_URL="https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-${ARCH}-enterprise-ubuntu2204-${MONGODB_VERSION}.tgz"
        LIB_NAME="mongo_crypt_v1.so"
        ;;
    *)
        echo -e "${RED}Error: Unsupported platform: $PLATFORM${NC}"
        echo "Supported platforms: darwin (macOS), linux"
        exit 1
        ;;
esac

echo -e "${YELLOW}MongoDB Version:${NC} $MONGODB_VERSION"
echo -e "${YELLOW}Download URL:${NC} $DOWNLOAD_URL"
echo ""

# Create lib directory
mkdir -p "$LIB_DIR"

# Download the library
echo -e "${BLUE}Downloading crypt_shared library...${NC}"
TEMP_FILE=$(mktemp)
curl -L --progress-bar -o "$TEMP_FILE" "$DOWNLOAD_URL"

if [ ! -f "$TEMP_FILE" ] || [ ! -s "$TEMP_FILE" ]; then
    echo -e "${RED}Error: Download failed${NC}"
    exit 1
fi

# Extract the library
echo -e "${BLUE}Extracting library...${NC}"
TEMP_DIR=$(mktemp -d)
tar -xzf "$TEMP_FILE" -C "$TEMP_DIR"

# Find and copy the library
LIB_FILE=$(find "$TEMP_DIR" -name "$LIB_NAME" -type f | head -1)

if [ -z "$LIB_FILE" ]; then
    echo -e "${RED}Error: Could not find $LIB_NAME in downloaded archive${NC}"
    rm -rf "$TEMP_DIR" "$TEMP_FILE"
    exit 1
fi

cp "$LIB_FILE" "$LIB_DIR/"
chmod 755 "$LIB_DIR/$LIB_NAME"

# Clean up
rm -rf "$TEMP_DIR" "$TEMP_FILE"

# Get absolute path
ABS_LIB_PATH="$(cd "$LIB_DIR" && pwd)/$LIB_NAME"

echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  CSFLE Library Installed!${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo -e "${YELLOW}Library location:${NC} $ABS_LIB_PATH"
echo ""
echo -e "${BLUE}Add to your .env file:${NC}"
echo ""
echo "  CRYPT_SHARED_LIB_PATH=$ABS_LIB_PATH"
echo ""

# Generate key if requested
if [ "$1" == "--generate-key" ]; then
    echo -e "${BLUE}Generating local master key...${NC}"
    echo ""
    
    # Check if Python is available
    if command -v python3 &> /dev/null; then
        PYTHON_CMD="python3"
    elif command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo -e "${YELLOW}Warning: Python not found. Generate key manually:${NC}"
        echo ""
        echo "  python -c 'from mdb_engine.core.csfle import generate_local_master_key; print(f\"MDB_CSFLE_LOCAL_KEY={generate_local_master_key()}\")'"
        echo ""
        exit 0
    fi
    
    # Try to generate key using mdb_engine
    KEY=$($PYTHON_CMD -c "
try:
    from mdb_engine.core.csfle import generate_local_master_key
    print(generate_local_master_key())
except ImportError:
    import secrets, base64
    print(base64.b64encode(secrets.token_bytes(96)).decode())
" 2>/dev/null)
    
    if [ -n "$KEY" ]; then
        echo -e "${YELLOW}Local master key (save this securely!):${NC}"
        echo ""
        echo "  MDB_CSFLE_LOCAL_KEY=$KEY"
        echo ""
        echo -e "${BLUE}Add both to your .env file:${NC}"
        echo ""
        echo "  CRYPT_SHARED_LIB_PATH=$ABS_LIB_PATH"
        echo "  MDB_CSFLE_LOCAL_KEY=$KEY"
        echo ""
    fi
fi

echo -e "${BLUE}Next steps:${NC}"
echo ""
echo "  1. Add the environment variables to your .env file"
echo "  2. Add 'encrypted': true to your memory_config in manifest.json"
echo "  3. Restart your application"
echo ""
echo -e "${GREEN}Done!${NC}"
