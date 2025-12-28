# Testing Guide

Comprehensive guide to testing in MDB Engine, including test structure, running tests, coverage reporting, and best practices.

## Table of Contents

- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Test Coverage](#test-coverage)
- [Writing Tests](#writing-tests)
- [Test Markers and Fixtures](#test-markers-and-fixtures)
- [CI/CD Integration](#cicd-integration)
- [Troubleshooting](#troubleshooting)

## Overview

MDB Engine uses pytest for testing with a focus on:
- **Unit tests**: Fast, isolated tests with mocked dependencies
- **Integration tests**: Tests that require MongoDB (using testcontainers)
- **Coverage**: Minimum 70% coverage requirement for core modules
- **Quality**: All tests must pass before merging PRs

## Test Structure

```
tests/
├── conftest.py          # Shared pytest fixtures and configuration
├── unit/                # Unit tests (fast, isolated)
│   ├── test_engine.py
│   ├── test_manifest.py
│   └── ...
└── integration/         # Integration tests (require MongoDB)
    └── ...
```

### Test Organization

- **Unit tests** (`tests/unit/`): Fast tests that mock external dependencies
  - No MongoDB connection required
  - Use mocks for database operations
  - Should run in < 1 second each

- **Integration tests** (`tests/integration/`): Tests with real MongoDB
  - Require Docker (uses testcontainers)
  - Test actual database operations
  - May take longer to run

## Running Tests

**We recommend using Makefile commands** for consistency and ease of use.

### Quick Start

```bash
# Install development dependencies
make install-dev

# Run quick quality check (lint + unit tests)
make check

# Run all tests
make test
```

### Available Test Commands

```bash
# Run all tests (unit + integration)
make test

# Run unit tests only (fast, no MongoDB)
make test-unit

# Run integration tests only (requires Docker)
make test-integration

# Run tests with coverage report (terminal)
make test-coverage

# Run tests with HTML coverage report
make test-coverage-html
# Then open htmlcov/index.html in your browser
```

### Direct pytest Commands

If you need more control, you can run pytest directly:

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/unit/test_engine.py -v

# Run tests matching a pattern
pytest -k "test_engine" -v

# Run tests with specific marker
pytest -m unit -v
pytest -m integration -v

# Run with verbose output and show print statements
pytest -v -s
```

### When to Use Each Command

- **`make test-unit`**: During development for fast feedback
- **`make test-integration`**: Before committing to verify integration
- **`make test-coverage-html`**: Before PR to check coverage
- **`make check`**: Quick quality check before committing

## Test Coverage

### Coverage Configuration

The Makefile tracks coverage for core modules:
- `mdb_engine/core` - Core engine functionality
- `mdb_engine/database` - Database abstraction and scoping

**Minimum threshold**: 70% (enforced by Makefile)

### Coverage Reports

**Terminal Report:**
```bash
make test-coverage
```
Shows coverage summary with missing lines highlighted.

**HTML Report:**
```bash
make test-coverage-html
```
Generates detailed HTML report at `htmlcov/index.html` with:
- Line-by-line coverage
- File-level coverage percentages
- Missing line indicators

### Coverage Goals

- **Minimum**: 70% (enforced)
- **Target**: 80%+ coverage
- **Critical paths**: 90%+ coverage
  - MongoDBEngine initialization
  - Manifest validation
  - Database scoping
  - Error handling

### Interpreting Coverage

- **Green lines**: Covered by tests
- **Red lines**: Not covered (add tests!)
- **Yellow lines**: Partially covered (some branches missed)

Focus on increasing coverage for:
- Error handling paths
- Edge cases
- Critical business logic

## Writing Tests

### Test Naming Convention

Use descriptive test names following the pattern:
```
test_function_name_scenario_expected_result
```

Examples:
```python
test_insert_one_operation_failure_raises_mongodb_engine_error
test_get_scoped_db_returns_scoped_wrapper
test_manifest_validation_rejects_invalid_schema
```

### Unit Test Example

```python
import pytest
from mdb_engine.database.abstraction import Collection
from mdb_engine.exceptions import MongoDBEngineError
from pymongo.errors import OperationFailure

@pytest.mark.asyncio
async def test_insert_one_operation_failure_raises_mongodb_engine_error(mock_scoped_collection):
    """Test that OperationFailure is caught and re-raised as MongoDBEngineError."""
    collection = Collection(mock_scoped_collection)
    mock_scoped_collection.insert_one.side_effect = OperationFailure("Error", 1)
    
    with pytest.raises(MongoDBEngineError) as exc_info:
        await collection.insert_one({"test": "data"})
    
    assert "Failed to insert document" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None  # Context preserved
```

### Integration Test Example

```python
import pytest
from mdb_engine.core.engine import MongoDBEngine

@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_mongodb_connection(mongodb_container):
    """Test engine initialization with real MongoDB."""
    engine = MongoDBEngine(
        mongo_uri=mongodb_container.get_connection_url(),
        db_name="test_db"
    )
    
    await engine.initialize()
    assert engine._initialized is True
    
    # Test actual database operation
    db = engine.get_scoped_db("test_app")
    result = await db.test_collection.insert_one({"test": "data"})
    assert result.inserted_id is not None
```

### Best Practices

1. **Test both success and failure paths**
   ```python
   async def test_insert_one_success():
       # Test successful insertion
       pass
   
   async def test_insert_one_failure():
       # Test failure handling
       pass
   ```

2. **Use descriptive assertions**
   ```python
   # Good
   assert result.inserted_id is not None
   assert "Failed to insert" in str(exc_info.value)
   
   # Avoid
   assert result
   assert exc_info.value
   ```

3. **Mock external dependencies**
   ```python
   # Mock MongoDB operations
   mock_collection.insert_one.return_value = InsertOneResult(inserted_id=ObjectId())
   ```

4. **Test exception handling**
   ```python
   with pytest.raises(MongoDBEngineError) as exc_info:
       await collection.insert_one(data)
   assert exc_info.value.__cause__ is not None
   ```

5. **Use fixtures for common setup**
   ```python
   @pytest.fixture
   def sample_data():
       return {"name": "test", "value": 123}
   ```

## Test Markers and Fixtures

### Test Markers

Tests are marked with pytest markers:

- **`@pytest.mark.unit`**: Unit tests (fast, no external dependencies)
- **`@pytest.mark.integration`**: Integration tests (require MongoDB)
- **`@pytest.mark.slow`**: Slow running tests
- **`@pytest.mark.asyncio`**: Async tests (automatically detected)

Example:
```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_fast_unit_test():
    pass

@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_test():
    pass
```

### Common Fixtures

Fixtures are defined in `tests/conftest.py`:

- **`mock_mongo_client`**: Mock MongoDB client
- **`mock_mongo_database`**: Mock MongoDB database
- **`mock_mongo_collection`**: Mock MongoDB collection
- **`mongodb_engine`**: Initialized MongoDBEngine instance
- **`scoped_db`**: ScopedMongoWrapper instance
- **`sample_manifest`**: Valid sample manifest
- **`mongodb_container`**: MongoDB testcontainer (integration tests)

Example usage:
```python
@pytest.mark.asyncio
async def test_with_fixtures(mongodb_engine, sample_manifest):
    await mongodb_engine.register_app(sample_manifest)
    assert mongodb_engine.is_app_registered("my_app")
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: make install-dev
    
    - name: Run linting
      run: make lint
    
    - name: Run unit tests
      run: make test-unit
    
    - name: Run integration tests
      run: make test-integration
    
    - name: Generate coverage report
      run: make test-coverage
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        files: ./htmlcov/index.html
```

### Pre-commit Checks

Before committing, run:

```bash
# Quick quality check
make check

# Or individually:
make format  # Format code
make lint    # Check linting
make test-unit  # Run unit tests
```

## Troubleshooting

### Tests Fail with Import Errors

**Problem**: `ModuleNotFoundError` or import errors

**Solution**:
```bash
# Make sure you're in the project root
cd /path/to/mdb-runtime

# Install in development mode
make install-dev

# Verify installation
python -c "import mdb_engine; print(mdb_engine.__version__)"
```

### Integration Tests Fail

**Problem**: Integration tests fail with connection errors

**Solution**:
```bash
# Check Docker is running
docker ps

# Make sure testcontainers can start containers
docker run hello-world

# Run integration tests with verbose output
make test-integration
# Or: pytest tests/integration/ -v -s
```

### Coverage Below Threshold

**Problem**: `make test-coverage` fails with "Coverage too low"

**Solution**:
1. Check coverage report: `make test-coverage-html`
2. Open `htmlcov/index.html` to see uncovered lines
3. Add tests for uncovered code
4. Focus on critical paths first

### Slow Test Execution

**Problem**: Tests take too long to run

**Solution**:
```bash
# Run only unit tests (fast)
make test-unit

# Run specific test file
pytest tests/unit/test_specific.py -v

# Run tests in parallel (if pytest-xdist installed)
pytest -n auto
```

### Linting Fails

**Problem**: `make lint` fails

**Solution**:
```bash
# Auto-fix formatting issues
make format

# Check what's wrong
make lint-check  # Non-failing version

# Fix specific issues
black mdb_engine tests scripts
isort mdb_engine tests scripts
```

## Additional Resources

- [tests/README.md](../../tests/README.md) - Test structure and examples
- [CONTRIBUTING.md](../../CONTRIBUTING.md#testing) - Testing guidelines for contributors
- [SETUP.md](../../SETUP.md#testing) - Development setup and testing
- [pytest Documentation](https://docs.pytest.org/) - Official pytest docs

## Questions?

If you have questions about testing:
1. Check existing test examples in `tests/unit/` and `tests/integration/`
2. Review [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines
3. Open an issue for discussion
4. Reach out to maintainers

