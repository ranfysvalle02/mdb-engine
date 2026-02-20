#!/bin/bash
# Script to format files according to CI's ruff version
# Run this after upgrading ruff: pip install --upgrade "ruff>=0.4.0"

set -e

echo "🔧 Formatting files to match CI requirements..."
echo ""

# Check ruff version
if ! command -v ruff >/dev/null 2>&1; then
    echo "❌ Error: ruff not found. Install with: pip install --upgrade 'ruff>=0.4.0'"
    exit 1
fi

RUFF_VERSION=$(ruff --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "unknown")
echo "Current ruff version: $RUFF_VERSION"
echo "Required: >=0.4.0"
echo ""

# Format all source files (same as make format)
echo "Formatting all source files..."
ruff format mdb_engine tests scripts

echo ""
echo "✅ Formatting complete!"
echo ""
echo "Verifying formatting..."
if ruff format --check mdb_engine tests scripts >/dev/null 2>&1; then
    echo "✅ All files are properly formatted!"
else
    echo "⚠️  Some files still need formatting. Run 'ruff format mdb_engine tests scripts' again."
    exit 1
fi

echo ""
echo "Next steps:"
echo "  1. Review changes: git diff"
echo "  2. Stage changes: git add ."
echo "  3. Commit: git commit -m 'Format code with ruff 0.4.0+'"
echo "  4. Push: git push"
