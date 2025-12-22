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

### Install Test Dependencies

```bash
pip install -e ".[test]"
```

### Run All Tests

```bash
pytest
```

### Run Unit Tests Only

```bash
pytest tests/unit/
```

### Run Integration Tests Only

```bash
pytest tests/integration/ -m integration
```

### Run with Coverage

```bash
pytest --cov=mdb_engine --cov-report=html
```

### Run Specific Test File

```bash
pytest tests/unit/test_engine.py
```

### Run Specific Test

```bash
pytest tests/unit/test_engine.py::TestMongoDBEngineInitialization::test_engine_initialization_success
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

## Continuous Integration

Tests should be run in CI/CD pipelines:

```yaml
# Example GitHub Actions
- name: Run tests
  run: |
    pip install -e ".[test]"
    pytest --cov=mdb_engine --cov-report=xml
```

## Test Coverage Goals

- Target: 80%+ coverage
- Critical paths: 90%+ coverage
- Focus areas:
  - MongoDBEngine initialization and lifecycle
  - Manifest validation
  - Database scoping
  - Error handling

