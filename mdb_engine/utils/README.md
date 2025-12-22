# Utilities Module

Internal utility functions for validation and error handling in MDB_ENGINE. These are helper functions used internally by other modules.

## Features

- **Validation Utilities**: Collection names, app slugs, MongoDB URIs
- **Error Handling Decorators**: MongoDB error handling and initialization checks
- **Internal Helpers**: Common patterns for error handling and validation

## Installation

The utils module is part of MDB_ENGINE. No additional installation required.

## Note

These utilities are primarily for **internal use** by MDB_ENGINE modules. They are documented here for reference, but most users will interact with them indirectly through other modules.

## Validation Functions

### Collection Name Validation

Validate and sanitize MongoDB collection names:

```python
from mdb_engine.utils.validation import validate_collection_name

# Valid collection names
name = validate_collection_name("users")           # ✅ Valid
name = validate_collection_name("user_profiles")    # ✅ Valid
name = validate_collection_name("data_2024")        # ✅ Valid

# Invalid collection names
try:
    validate_collection_name("users-collection")    # ❌ Hyphens not allowed
except ValueError as e:
    print(e)  # "Invalid collection name: users-collection. Only alphanumeric characters and underscores are allowed."

try:
    validate_collection_name("")                    # ❌ Empty string
except ValueError as e:
    print(e)  # "Collection name must be a non-empty string"

try:
    validate_collection_name("a" * 256)            # ❌ Too long
except ValueError as e:
    print(e)  # "Collection name too long: ... (max 255 characters)"
```

**Rules:**
- Must be non-empty string
- Only alphanumeric characters and underscores allowed
- Maximum 255 characters
- Case-sensitive (preserves case)

### App Slug Validation

Validate app slug format:

```python
from mdb_engine.utils.validation import validate_app_slug

# Valid slugs
slug = validate_app_slug("my_app")           # ✅ Valid
slug = validate_app_slug("my-app")           # ✅ Valid
slug = validate_app_slug("app123")           # ✅ Valid
slug = validate_app_slug("my_app_v2")        # ✅ Valid

# Invalid slugs
try:
    validate_app_slug("MyApp")               # ❌ Uppercase not allowed
except ValueError as e:
    print(e)  # "Invalid app slug: MyApp. Slug must contain only lowercase letters, numbers, underscores, and hyphens."

try:
    validate_app_slug("my app")               # ❌ Spaces not allowed
except ValueError as e:
    print(e)

try:
    validate_app_slug("a" * 101)              # ❌ Too long
except ValueError as e:
    print(e)  # "App slug too long: ... (max 100 characters)"
```

**Rules:**
- Must be non-empty string
- Only lowercase letters, numbers, underscores, and hyphens
- Maximum 100 characters
- Must be lowercase

### MongoDB URI Validation

Basic validation for MongoDB connection URIs:

```python
from mdb_engine.utils.validation import validate_mongo_uri

# Valid URIs
uri = validate_mongo_uri("mongodb://localhost:27017")                    # ✅ Valid
uri = validate_mongo_uri("mongodb+srv://cluster.mongodb.net/")           # ✅ Valid
uri = validate_mongo_uri("mongodb://user:pass@host:27017/db")            # ✅ Valid

# Invalid URIs
try:
    validate_mongo_uri("postgresql://localhost:5432")                     # ❌ Wrong protocol
except ValueError as e:
    print(e)  # "Invalid MongoDB URI format: ... URI must start with 'mongodb://' or 'mongodb+srv://'"

try:
    validate_mongo_uri("")                                                # ❌ Empty string
except ValueError as e:
    print(e)  # "MongoDB URI must be a non-empty string"
```

**Rules:**
- Must be non-empty string
- Must start with `mongodb://` or `mongodb+srv://`
- Does not validate full URI structure (basic format check only)

## Decorators

### MongoDB Error Handling

Decorator for consistent MongoDB error handling:

```python
from mdb_engine.utils.decorators import handle_mongo_errors
from pymongo.errors import OperationFailure, AutoReconnect

@handle_mongo_errors
async def my_database_operation(db, document):
    """
    This decorator automatically:
    - Catches OperationFailure and logs with details
    - Catches AutoReconnect and logs warning
    - Catches other exceptions and logs error
    - Re-raises all exceptions for caller to handle
    """
    result = await db.collection.insert_one(document)
    return result

# Usage
try:
    result = await my_database_operation(db, {"name": "test"})
except OperationFailure as e:
    # Handle operation failure
    print(f"Operation failed: {e.details}")
except AutoReconnect as e:
    # Handle reconnection
    print(f"Reconnecting: {e}")
```

**What it does:**
- Catches `OperationFailure` and logs error with details
- Catches `AutoReconnect` and logs warning
- Catches other exceptions and logs error
- Re-raises all exceptions (caller must handle)

### Initialization Check

Decorator to ensure engine is initialized:

```python
from mdb_engine.utils.decorators import require_initialized

class MyEngine:
    def __init__(self):
        self._initialized = False
    
    async def initialize(self):
        # ... initialization logic ...
        self._initialized = True
    
    @require_initialized
    async def do_something(self):
        """
        This method can only be called after initialize().
        The decorator checks _initialized attribute and raises
        RuntimeError if not initialized.
        """
        # ... method implementation ...
        pass

# Usage
engine = MyEngine()

try:
    await engine.do_something()  # ❌ Raises RuntimeError
except RuntimeError as e:
    print(e)  # "MyEngine not initialized. Call initialize() first."

await engine.initialize()
await engine.do_something()  # ✅ Works
```

**What it does:**
- Checks for `_initialized` attribute on instance
- Raises `RuntimeError` if not initialized
- Allows method execution if initialized

## API Reference

### Validation Functions

#### `validate_collection_name(name: str) -> str`

Validate and sanitize collection name.

**Parameters:**
- `name`: Collection name to validate

**Returns:** Validated collection name

**Raises:** `ValueError` if invalid

**Rules:**
- Non-empty string
- Alphanumeric and underscores only
- Max 255 characters

#### `validate_app_slug(slug: str) -> str`

Validate app slug format.

**Parameters:**
- `slug`: App slug to validate

**Returns:** Validated slug

**Raises:** `ValueError` if invalid

**Rules:**
- Non-empty string
- Lowercase letters, numbers, underscores, hyphens only
- Max 100 characters

#### `validate_mongo_uri(uri: str) -> str`

Basic validation for MongoDB URI format.

**Parameters:**
- `uri`: MongoDB connection URI

**Returns:** Validated URI

**Raises:** `ValueError` if invalid

**Rules:**
- Non-empty string
- Must start with `mongodb://` or `mongodb+srv://`

### Decorators

#### `@handle_mongo_errors`

Decorator for consistent MongoDB error handling.

**What it does:**
- Catches `OperationFailure` and logs error
- Catches `AutoReconnect` and logs warning
- Catches other exceptions and logs error
- Re-raises all exceptions

**Usage:**
```python
@handle_mongo_errors
async def my_function():
    # MongoDB operations
    pass
```

#### `@require_initialized`

Decorator to ensure engine is initialized.

**What it does:**
- Checks `_initialized` attribute
- Raises `RuntimeError` if not initialized

**Usage:**
```python
@require_initialized
async def my_method(self):
    # Method implementation
    pass
```

## Usage Examples

### Input Validation

Validate user input before database operations:

```python
from mdb_engine.utils.validation import validate_collection_name, validate_app_slug

def create_collection(db, collection_name, app_slug):
    # Validate inputs
    validated_name = validate_collection_name(collection_name)
    validated_slug = validate_app_slug(app_slug)
    
    # Use validated values
    collection = db[validated_name]
    # ... create collection with app_slug ...
```

### Error Handling

Use decorators for consistent error handling:

```python
from mdb_engine.utils.decorators import handle_mongo_errors

@handle_mongo_errors
async def safe_insert(db, document):
    """Insert with automatic error logging."""
    return await db.collection.insert_one(document)

# Caller handles exceptions
try:
    result = await safe_insert(db, {"name": "test"})
except OperationFailure as e:
    # Handle operation failure
    pass
```

### Initialization Checks

Ensure methods are called after initialization:

```python
from mdb_engine.utils.decorators import require_initialized

class DatabaseService:
    def __init__(self):
        self._initialized = False
    
    async def initialize(self):
        # ... setup ...
        self._initialized = True
    
    @require_initialized
    async def query(self, filter):
        """Can only be called after initialize()."""
        return await self.db.collection.find(filter).to_list(length=10)
```

## Best Practices

1. **Validate early** - Validate inputs as early as possible
2. **Use decorators** - Use error handling decorators for consistency
3. **Handle exceptions** - Decorators log but don't handle exceptions
4. **Check initialization** - Use `@require_initialized` for methods that need initialization
5. **Follow naming rules** - Use validated names for collections and slugs
6. **Log errors** - Decorators log errors automatically, but you may want additional logging

## Internal Use

These utilities are primarily used internally by:

- **`core/`** - MongoDBEngine uses validation and initialization checks
- **`database/`** - Database module uses collection name validation
- **`auth/`** - Auth module uses app slug validation
- **`indexes/`** - Index module uses validation functions

Most users will interact with these utilities indirectly through other modules.

## Related Modules

- **`core/`** - Uses validation and initialization decorators
- **`database/`** - Uses collection name validation
- **`auth/`** - Uses app slug validation

