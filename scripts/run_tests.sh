#!/bin/bash
# Test runner script for MDB_ENGINE
# Run from project root: ./scripts/run_tests.sh

set -e

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "🧪 MDB_ENGINE Test Runner"
echo "=========================="
echo ""

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest not found. Installing test dependencies..."
    pip install -e ".[test]"
fi

# Parse arguments
TEST_TYPE="${1:-all}"
COVERAGE="${2:-false}"

case "$TEST_TYPE" in
    unit)
        echo "📦 Running unit tests..."
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/unit/ -v --cov=mdb_engine --cov-report=term-missing --cov-report=html
        else
            pytest tests/unit/ -v
        fi
        ;;
    integration)
        echo "🔗 Running integration tests..."
        pytest tests/integration/ -v -m integration
        ;;
    all)
        echo "🚀 Running all tests..."
        if [ "$COVERAGE" = "true" ]; then
            pytest tests/ -v --cov=mdb_engine --cov-report=term-missing --cov-report=html
        else
            pytest tests/ -v
        fi
        ;;
    *)
        echo "Usage: $0 [unit|integration|all] [coverage]"
        echo "  unit       - Run unit tests only"
        echo "  integration - Run integration tests only"
        echo "  all        - Run all tests (default)"
        echo "  coverage   - Set to 'true' to generate coverage report"
        exit 1
        ;;
esac

echo ""
echo "✅ Tests completed!"

