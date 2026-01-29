# Quick Test Commands for Multi-App Mounting

## 🚀 Quick Test (Run This First)

```bash
# Test the new multi-app mounting feature
pytest tests/unit/test_multi_app_mounting.py -v

# Test that existing functionality still works
pytest tests/unit/test_engine.py tests/unit/test_engine_unified.py -v
pytest tests/unit/test_app_registration.py -v
pytest tests/unit/test_manifest.py -v
```

## ✅ Full Regression Test Suite

```bash
# Run all related tests to ensure nothing broke
pytest tests/unit/test_multi_app_mounting.py \
       tests/unit/test_engine.py \
       tests/unit/test_engine_unified.py \
       tests/unit/test_app_registration.py \
       tests/unit/test_manifest.py \
       tests/unit/test_auth_modes.py \
       -v --tb=line

# Expected: 156 passed ✅
```

## 🎯 Using Make Commands

```bash
# Run all unit tests (includes multi-app tests)
make test-unit

# Run with coverage
make test-coverage

# Quick quality check (format + lint + unit tests)
make check
```

## 📊 Test Coverage

- **Multi-app mounting**: 14 tests ✅
- **Engine core**: 91 tests ✅
- **App registration**: 12 tests ✅
- **Manifest validation**: 26 tests ✅
- **Auth modes**: 13 tests ✅

**Total**: 156 tests, all passing ✅

## 🔍 What Gets Tested

1. ✅ Path prefix validation (conflicts, reserved paths, duplicates)
2. ✅ Programmatic multi-app creation
3. ✅ Manifest-based multi-app creation
4. ✅ Shared auth initialization in multi-app context
5. ✅ Health check endpoint
6. ✅ Sub-app mode (is_sub_app parameter)
7. ✅ Backward compatibility (existing create_app() still works)
8. ✅ Manifest schema validation (multi_app field)

## 🐛 If Tests Fail

1. Check environment variables are set:
   ```bash
   export MDB_ENGINE_JWT_SECRET="test_secret_$(openssl rand -hex 32)"
   export MDB_ENGINE_MASTER_KEY="$(openssl rand -base64 32)"
   ```

2. Run with verbose output:
   ```bash
   pytest tests/unit/test_multi_app_mounting.py -v -s
   ```

3. Check specific test:
   ```bash
   pytest tests/unit/test_multi_app_mounting.py::TestPathPrefixValidation -v
   ```
