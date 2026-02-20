#!/bin/bash

# Script to install Git hooks from the githooks directory
# This makes hooks shareable across the team

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GITHOOKS_DIR="$PROJECT_ROOT/githooks"
GIT_HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

if [ ! -d "$GIT_HOOKS_DIR" ]; then
    echo "❌ Error: .git/hooks directory not found. Are you in a Git repository?"
    exit 1
fi

if [ ! -d "$GITHOOKS_DIR" ]; then
    echo "❌ Error: githooks directory not found at $GITHOOKS_DIR"
    exit 1
fi

echo "📦 Installing Git hooks from $GITHOOKS_DIR..."

# Install each hook from githooks directory
for hook in "$GITHOOKS_DIR"/*; do
    if [ -f "$hook" ] && [ -x "$hook" ]; then
        hook_name="$(basename "$hook")"
        target="$GIT_HOOKS_DIR/$hook_name"
        
        echo "  Installing $hook_name..."
        cp "$hook" "$target"
        chmod +x "$target"
        
        echo "  ✅ Installed $hook_name"
    fi
done

echo ""
echo "✅ All hooks installed successfully!"
echo ""
echo "The following hooks are now active:"
ls -1 "$GIT_HOOKS_DIR" | grep -v "\.sample$" | sed 's/^/  - /'

