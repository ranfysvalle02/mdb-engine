# MDB-Engine Code Quality Issues & Bugs

This document tracks bugs, logic issues, hardcoded values, and poor code patterns found during codebase scanning.

**Last Updated:** February 5, 2026

---

## 🧪 Example Testing Checklist

### TEST EVERY EXAMPLE, MAKE SURE THEY ARE ALL WORKING

#### Basic Examples
- [ ] `examples/basic/chit_chat`
- [ ] `examples/basic/gdpr_demo`
- [ ] `examples/basic/graphs-mdb`
- [ ] `examples/basic/interactive_rag`
- [ ] `examples/basic/memory_kitchen_sink`
- [ ] `examples/basic/oso_hello_world`
- [ ] `examples/basic/parallax`
- [ ] `examples/basic/parallax_memory`
- [ ] `examples/basic/redaction_demo`
- [ ] `examples/basic/vector_hacking`

#### Advanced Examples
- [ ] `examples/advanced/simple_app`
- [x] `examples/advanced/sso-multi-app` (✅ Known to work)
- [ ] `examples/advanced/websocket-tickets`

---

## 🔴 Critical Issues

### 1. Synchronous Database Operations in Sync Methods (Blocking Event Loop)
**Location:** `mdb_engine/memory/cognitive.py`
**Lines:** 825, 5690, 5949-5950

**Issue:** Synchronous `count_documents()` calls in sync methods that may be called from async contexts, blocking the event loop.

```python
# Line 825
count = self.collection.count_documents({})  # Blocks event loop

# Line 5690  
any_memories = self.collection.count_documents({})  # Blocks event loop

# Lines 5949-5950
total_count = self.collection.count_documents({})  # Blocks event loop
user_count = self.collection.count_documents(query) if query else total_count  # Blocks event loop
```

**Impact:** These methods (`__init__`, `search`, `get_all`) are synchronous but may be called from async FastAPI routes. This blocks the event loop and degrades performance.

**Recommendation:** 
- Option 1: Make these methods async and use `await collection.count_documents()`
- Option 2: If sync is required, wrap in `asyncio.to_thread()` when called from async contexts
- Option 3: Use async versions of these methods where available

**Priority:** HIGH - Performance impact

---

### 2. Redundant Allow-List Check
**Location:** `mdb_engine/memory/redaction.py`
**Lines:** 198-204

**Issue:** Double-checking allow-list after already filtering it out.

```python
# Line 198: Already filters out allow-listed values
matches_to_redact = [m for m in matches if m not in self.allow_list]

if matches_to_redact:
    # Replace matches
    for match in matches_to_redact:
        if match in self.allow_list:  # Line 203: Redundant check!
            continue
        redacted_text = redacted_text.replace(match, self.replacement)
```

**Impact:** Unnecessary computation on every redaction operation.

**Recommendation:** Remove the redundant check on line 203.

**Priority:** LOW - Minor performance issue

---

### 3. Busy-Waiting Pattern
**Location:** `mdb_engine/core/engine.py`
**Lines:** 4374-4377

**Issue:** Busy-waiting with `asyncio.sleep(0.01)` in a loop is inefficient.

```python
while self._shared_user_pool_initializing:
    import asyncio  # Line 4375: Import inside function
    await asyncio.sleep(0.01)  # Busy-waiting
```

**Impact:** 
- Wastes CPU cycles
- Import statement inside function (should be at module level)
- Could use an event or condition variable instead

**Recommendation:** 
- Use `asyncio.Event` or `asyncio.Condition` for proper synchronization
- Move `import asyncio` to module level

**Priority:** MEDIUM - Performance and code quality

---

## 🟡 Hardcoded Values (Magic Numbers)

### 4. Hardcoded Embedding Dimensions
**Location:** Multiple files
**Default Value:** `1536`

**Files:**
- `mdb_engine/core/service_initialization.py`: Lines 46, 336, 519, 675, 778
- `mdb_engine/memory/cognitive.py`: Line 764

**Issue:** Hardcoded embedding dimension `1536` scattered throughout codebase.

**Recommendation:** 
- Move to `mdb_engine/constants.py` as `DEFAULT_EMBEDDING_DIMS`
- Reference constant instead of hardcoding

**Priority:** LOW - Code maintainability

---

### 5. Hardcoded Similarity Thresholds
**Location:** `mdb_engine/memory/cognitive.py`
**Lines:** 777-782

**Issue:** Multiple hardcoded threshold values:

```python
self.similarity_threshold = self.config.get("similarity_threshold", 0.7)
self.reinforcement_factor = self.config.get("reinforcement_factor", 1.1)
self.decay_factor = self.config.get("decay_factor", 0.99)
self.merge_threshold_low = self.config.get("merge_threshold_low", 0.7)
self.merge_threshold_high = self.config.get("merge_threshold_high", 0.85)
self.duplicate_threshold = self.config.get("duplicate_threshold", 0.90)
```

**Recommendation:** 
- Define constants in `mdb_engine/constants.py`:
  - `DEFAULT_SIMILARITY_THRESHOLD = 0.7`
  - `DEFAULT_REINFORCEMENT_FACTOR = 1.1`
  - `DEFAULT_DECAY_FACTOR = 0.99`
  - `DEFAULT_MERGE_THRESHOLD_LOW = 0.7`
  - `DEFAULT_MERGE_THRESHOLD_HIGH = 0.85`
  - `DEFAULT_DUPLICATE_THRESHOLD = 0.90`

**Priority:** LOW - Code maintainability

---

### 6. Hardcoded Flashbulb Threshold
**Location:** `mdb_engine/memory/cognitive.py`
**Line:** 129

**Issue:** Class constant `FLASHBULB_THRESHOLD = 0.7` is hardcoded.

**Recommendation:** Make configurable via manifest/config.

**Priority:** LOW

---

## 🟠 Code Quality Issues

### 7. Import Statements Inside Functions
**Location:** Multiple files

**Issue:** `import asyncio` statements inside functions instead of at module level.

**Files:**
- `mdb_engine/core/engine.py`: Lines 174, 412, 4375
- `mdb_engine/memory/graph.py`: Line 1028
- `mdb_engine/core/manifest.py`: Line 3763
- `mdb_engine/indexes/manager.py`: Line 105, 785
- `mdb_engine/core/app_secrets.py`: Lines 125, 261
- `mdb_engine/auth/oso_factory.py`: Lines 45, 213

**Impact:** 
- Slower execution (imports checked on every call)
- Poor code style
- Makes it harder to track dependencies

**Recommendation:** Move all `import asyncio` statements to module level.

**Priority:** LOW - Code style

---

### 8. Code Duplication: Redaction Patterns
**Location:** 
- `mdb_engine/memory/redaction.py`: Lines 71-95
- `mdb_engine/redaction/regexp.py`: Lines 46-70

**Issue:** Identical regex patterns defined in two places.

**Impact:** 
- Maintenance burden (changes must be made in two places)
- Risk of divergence
- Code duplication

**Recommendation:** 
- Extract patterns to a shared module (e.g., `mdb_engine/redaction/patterns.py`)
- Import from shared location in both files

**Priority:** MEDIUM - Maintainability

---

### 9. TODO Comment Left in Code
**Location:** `mdb_engine/auth/csrf.py`
**Line:** 950

**Issue:** TODO comment indicates incomplete implementation.

```python
# TODO: Implement request.form() based extraction if needed.
```

**Recommendation:** 
- Either implement the feature or remove the TODO
- If keeping, add to proper issue tracker

**Priority:** LOW

---

## 🔵 Potential Logic Issues

### 10. Potential None Access in Memory Search
**Location:** `mdb_engine/memory/cognitive.py`
**Line:** 2821-2823

**Issue:** Accessing `.isoformat()` on potentially None value, though guarded.

```python
"created_at": doc.get("created_at").isoformat()
    if doc.get("created_at")
    else None,
```

**Note:** This is actually safe due to the ternary, but could be clearer.

**Recommendation:** Consider using:
```python
created_at = doc.get("created_at")
"created_at": created_at.isoformat() if created_at else None,
```

**Priority:** VERY LOW - Code clarity

---

### 11. Synchronous Collection Operations in Async Context
**Location:** `mdb_engine/memory/cognitive.py`
**Line:** 822

**Issue:** `list_collection_names()` is synchronous and may block.

```python
collections = self._db.list_collection_names()  # Sync operation
```

**Impact:** Blocks event loop if called from async context.

**Recommendation:** Use async version or wrap in `asyncio.to_thread()`.

**Priority:** MEDIUM - Performance

---

## 🟢 Security & Best Practices

### 12. Hardcoded Passwords in Examples
**Location:** Example files

**Issue:** Example configurations contain hardcoded passwords:
- `examples/basic/vector_hacking/api/config.py`: Line 29 - `"mongodb://admin:password@mongodb:27017/..."`
- `examples/basic/interactive_rag/env.example`: Line 2 - `MONGO_URI=mongodb://admin:password@mongodb:27017/...`
- Documentation examples showing `"password123"` as demo passwords

**Impact:** 
- Security risk if examples are copied to production
- Poor security practice demonstration

**Recommendation:** 
- Use environment variables in examples
- Add warnings about changing default passwords
- Use placeholder values like `CHANGE_ME` or `your-password-here`

**Priority:** LOW - Examples only, but important for security awareness

---

### 13. Exception Handling in Abstraction Layer
**Location:** `mdb_engine/database/abstraction.py`
**Lines:** 454-464

**Issue:** Catching broad exceptions and returning 0 or raising generic error.

```python
except (OperationFailure, ConnectionFailure, ServerSelectionTimeoutError):
    logger.exception("Database operation failed in count_documents")
    return 0  # Returns 0 on error - may hide bugs
except (InvalidOperation, TypeError, ValueError, AttributeError) as e:
    logger.exception("Error in count_documents")
    raise MongoDBEngineError(...) from e
```

**Impact:** 
- Returning 0 on database errors may hide real issues
- Callers may not know if 0 is a real count or an error

**Recommendation:** 
- Consider raising exception instead of returning 0
- Or return `None` and let caller handle
- Document behavior clearly

**Priority:** LOW - Design decision, but worth reviewing

---

## 📋 Summary by Priority

### HIGH Priority
1. Synchronous database operations blocking event loop (Issue #1)

### MEDIUM Priority
2. Busy-waiting pattern (Issue #3)
3. Code duplication in redaction patterns (Issue #8)
4. Synchronous collection operations (Issue #11)

### LOW Priority
5. Redundant allow-list check (Issue #2)
6. Hardcoded embedding dimensions (Issue #4)
7. Hardcoded similarity thresholds (Issue #5)
8. Hardcoded flashbulb threshold (Issue #6)
9. Import statements inside functions (Issue #7)
10. TODO comment (Issue #9)
11. Hardcoded passwords in examples (Issue #12)
12. Exception handling patterns (Issue #13)

### VERY LOW Priority
10. Potential None access clarity (Issue #10)

---

## 🔍 Additional Observations

### Good Practices Found
- ✅ Comprehensive error handling documentation (`docs/guides/error_handling.md`)
- ✅ Security-focused query validation
- ✅ Proper use of constants module for some values
- ✅ Good separation of concerns

### Areas for Improvement
- More consistent use of constants instead of magic numbers
- Better async/sync boundary management
- Reduced code duplication
- More comprehensive type hints in some areas

---

## 📝 Notes

- This scan focused on the `mdb_engine/` core codebase
- Example files were scanned but issues there are lower priority
- Some issues may be intentional design decisions - review before changing
- Consider running static analysis tools (mypy, pylint, bandit) for additional findings
