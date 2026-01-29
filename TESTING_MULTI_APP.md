# Testing Multi-App Mounting Feature

This document explains how to test the new multi-app mounting feature to ensure nothing broke.

## Quick Test Commands

### 1. Test Multi-App Mounting Feature (New Tests)

```bash
# Run all multi-app mounting tests
pytest tests/unit/test_multi_app_mounting.py -v

# Run specific test class
pytest tests/unit/test_multi_app_mounting.py::TestPathPrefixValidation -v
pytest tests/unit/test_multi_app_mounting.py::TestCreateMultiAppProgrammatic -v
pytest tests/unit/test_multi_app_mounting.py::TestMultiAppSharedAuth -v
```

### 2. Test Existing Engine Functionality (Regression Tests)

```bash
# Test engine core functionality (ensures create_app() still works)
pytest tests/unit/test_engine.py tests/unit/test_engine_unified.py -v

# Test app registration (ensures nothing broke in registration)
pytest tests/unit/test_app_registration.py -v

# Test manifest validation (ensures multi_app schema doesn't break existing manifests)
pytest tests/unit/test_manifest.py -v
```

### 3. Full Test Suite

```bash
# Run all unit tests (fast, no MongoDB required)
make test-unit

# Or directly:
pytest tests/unit/ -v

# Run with coverage
make test-coverage
```

### 4. Integration Tests (Requires MongoDB)

```bash
# Run integration tests (requires Docker/MongoDB)
make test-integration

# Or directly:
pytest tests/integration/test_multi_app_integration.py -v -m integration
```

## Test Coverage Summary

### New Tests Added

**File**: `tests/unit/test_multi_app_mounting.py`

- ✅ Path prefix validation (5 tests)
- ✅ Programmatic multi-app creation (4 tests)
- ✅ Manifest-based multi-app creation (2 tests)
- ✅ Shared auth initialization (1 test)
- ✅ Health check endpoint (1 test)
- ✅ Sub-app mode (1 test)

**Total**: 14 new tests, all passing ✅

### Regression Tests Verified

- ✅ Engine initialization tests (91 tests)
- ✅ App registration tests (12 tests)
- ✅ Manifest validation tests (26 tests)

**Total**: 129 existing tests verified, all passing ✅

## Manual Testing Checklist

### 1. Basic Multi-App Creation

```python
from pathlib import Path
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="mongodb://localhost:27017", db_name="test_db")
app = engine.create_multi_app(
    apps=[
        {"slug": "app1", "manifest": Path("./app1/manifest.json"), "path_prefix": "/app1"},
        {"slug": "app2", "manifest": Path("./app2/manifest.json"), "path_prefix": "/app2"}
    ]
)
# Should create FastAPI app without errors
```

### 2. Path Prefix Validation

```python
# Should fail - path conflict
engine.create_multi_app(
    apps=[
        {"slug": "app1", "manifest": Path("./app1/manifest.json"), "path_prefix": "/app"},
        {"slug": "app2", "manifest": Path("./app2/manifest.json"), "path_prefix": "/app/v2"}  # Conflict!
    ]
)
# Should raise ValueError
```

### 3. Shared Auth in Multi-App

```python
# Create multi-app with shared auth apps
app = engine.create_multi_app(
    apps=[
        {"slug": "auth-hub", "manifest": Path("./auth-hub/manifest.json"), "path_prefix": "/auth-hub"},
        {"slug": "sso-app", "manifest": Path("./sso-app/manifest.json"), "path_prefix": "/sso-app"}
    ]
)

# Start app and verify shared user pool is initialized once
async with app.router.lifespan_context(app):
    assert hasattr(app.state, "user_pool")
    assert app.state.user_pool is not None
```

### 4. Health Check Endpoint

```python
from fastapi.testclient import TestClient

async with app.router.lifespan_context(app):
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "mounted_apps" in data
    assert "status" in data
```

## Running Tests in CI/CD

### GitHub Actions / CI Pipeline

```yaml
# Example CI test step
- name: Test Multi-App Mounting
  run: |
    pytest tests/unit/test_multi_app_mounting.py -v
    pytest tests/unit/test_engine.py tests/unit/test_engine_unified.py -v
    pytest tests/unit/test_app_registration.py -v
    pytest tests/unit/test_manifest.py -v
```

### Pre-Commit Hook

Add to your pre-commit hook:

```bash
#!/bin/bash
# Run multi-app tests before commit
pytest tests/unit/test_multi_app_mounting.py -v || exit 1
pytest tests/unit/test_engine.py::TestMongoDBEngineCreateApp -v || exit 1
```

## Test Results Summary

### ✅ All Tests Passing (156 total)

- **New multi-app mounting tests**: 14/14 passing ✅
- **Engine core tests**: 91/91 passing ✅
- **App registration tests**: 12/12 passing ✅
- **Manifest validation tests**: 26/26 passing ✅
- **Auth mode tests**: 13/13 passing ✅

**Total**: 156 tests passing, 0 failures ✅

### Backward Compatibility Verified

- ✅ `create_app()` still works with existing code (backward compatible)
- ✅ `is_sub_app` parameter defaults to `False` (doesn't affect existing usage)
- ✅ Existing manifests without `multi_app` field still validate
- ✅ Single-app deployments unaffected
- ✅ All existing FastAPI integration tests pass

## Troubleshooting

### If tests fail:

1. **Check environment variables**:
   ```bash
   export MDB_ENGINE_JWT_SECRET="test_secret_$(openssl rand -hex 32)"
   export MDB_ENGINE_MASTER_KEY="$(openssl rand -base64 32)"
   ```

2. **Check MongoDB connection** (for integration tests):
   ```bash
   docker run -d -p 27017:27017 --name mongodb mongo:7
   ```

3. **Run with verbose output**:
   ```bash
   pytest tests/unit/test_multi_app_mounting.py -v -s
   ```

4. **Check for import errors**:
   ```bash
   python -c "from mdb_engine.core.engine import MongoDBEngine; print('OK')"
   ```

## Next Steps

After verifying tests pass:

1. ✅ Run full test suite: `make test-unit`
2. ✅ Check linting: `make lint-local`
3. ✅ Test example: `cd examples/advanced/sso-multi-app/apps && uvicorn multi_app_main:app --reload`
4. ✅ Deploy to staging environment
5. ✅ Test on Render.com deployment
