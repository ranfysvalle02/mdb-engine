# MDB_ENGINE Test Suite

This directory contains the test suite for MDB_ENGINE - MongoDB Multi-Tenant Engine.

## Test Structure

```
tests/
├── conftest.py          # Shared pytest fixtures and configuration
├── unit/                # Unit tests (fast, isolated)
│   ├── __init__.py
│   ├── test_engine.py
│   ├── test_manifest.py
│   └── test_exceptions.py
└── integration/         # Integration tests (require MongoDB)
    └── __init__.py
```

**Note:** Test runner script is located in `scripts/run_tests.sh`

## Running Tests

**We recommend using Makefile commands** for running tests. They provide consistent configuration and are easier to use.

### Install Test Dependencies

```bash
# Install with test dependencies
make install-dev

# Or manually:
pip install -e ".[test]"
```

### Run All Tests

```bash
# Using Makefile (recommended)
make test

# Or directly with pytest
pytest tests/ -v
```

### Run Unit Tests Only

```bash
# Using Makefile (recommended)
make test-unit

# Or directly with pytest
pytest tests/unit/ -v -m "not integration"
```

### Run Integration Tests Only

```bash
# Using Makefile (recommended)
# Note: Requires Docker
make test-integration

# Or directly with pytest
pytest tests/integration/ -v -m integration
```

### Run with Coverage

```bash
# Terminal coverage report (recommended)
make test-coverage

# HTML coverage report (recommended)
make test-coverage-html
# Then open htmlcov/index.html in your browser

# Or directly with pytest
pytest --cov=mdb_engine/core --cov=mdb_engine/database --cov-report=html
```

### Run Specific Test File

```bash
# Direct pytest command (Makefile doesn't support file-specific runs)
pytest tests/unit/test_engine.py -v
```

### Run Specific Test

```bash
# Direct pytest command
pytest tests/unit/test_engine.py::TestMongoDBEngineInitialization::test_engine_initialization_success -v
```

## Test Markers

Tests are marked with pytest markers:

- `@pytest.mark.unit` - Unit tests (fast, no external dependencies)
- `@pytest.mark.integration` - Integration tests (require MongoDB)
- `@pytest.mark.slow` - Slow running tests
- `@pytest.mark.asyncio` - Async tests (automatically detected)

## Fixtures

Common fixtures are defined in `conftest.py`:

- `mock_mongo_client` - Mock MongoDB client
- `mock_mongo_database` - Mock MongoDB database
- `mock_mongo_collection` - Mock MongoDB collection
- `mongodb_engine` - Initialized MongoDBEngine instance
- `scoped_db` - ScopedMongoWrapper instance
- `sample_manifest` - Valid sample manifest
- `sample_manifest_v1` - Valid v1.0 manifest
- `invalid_manifest` - Invalid manifest for testing validation

## Writing Tests

### Unit Test Example

```python
import pytest
from mdb_engine.core.engine import MongoDBEngine

class TestMongoDBEngine:
    @pytest.mark.asyncio
    async def test_initialization(self, mongodb_engine):
        assert mongodb_engine._initialized is True
```

### Integration Test Example

```python
import pytest

@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_mongodb_connection():
    # Test with real MongoDB instance
    pass
```

## Makefile Commands

The Makefile provides convenient commands for all testing operations:

```bash
# View all available commands
make help

# Testing commands
make test             # Run all tests (unit + integration)
make test-unit        # Run unit tests only (fast, no MongoDB)
make test-integration # Run integration tests only (requires Docker)
make test-coverage    # Run tests with coverage report (terminal)
make test-coverage-html # Run tests with HTML coverage report

# Quick quality check (lint + unit tests)
make check
```

### When to Use Each Command

- **`make test-unit`**: Fast feedback during development (no external dependencies)
- **`make test-integration`**: Full integration testing (requires Docker)
- **`make test-coverage-html`**: Before committing/PR (visual coverage report)
- **`make check`**: Quick quality check before committing (lint + unit tests)

## Continuous Integration

Tests should be run in CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Install dependencies
  run: make install-dev

- name: Run tests with coverage
  run: make test-coverage

# Or for HTML coverage report:
- name: Run tests with HTML coverage
  run: make test-coverage-html

- name: Upload coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./htmlcov/index.html
```

## Test Coverage Goals

- **Minimum threshold**: 70% (enforced by Makefile)
- **Target**: 80%+ coverage
- **Critical paths**: 90%+ coverage
- **Coverage modules**: 
  - `mdb_engine/core` - Core engine functionality
  - `mdb_engine/database` - Database abstraction and scoping
- **Focus areas**:
  - MongoDBEngine initialization and lifecycle
  - Manifest validation
  - Database scoping
  - Error handling

### Coverage Reports

Coverage reports are generated in:
- **Terminal**: `make test-coverage` - Shows coverage summary in terminal
- **HTML**: `make test-coverage-html` - Generates `htmlcov/index.html` with detailed line-by-line coverage

