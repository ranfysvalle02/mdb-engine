# Index Management Module

High-level index creation and management orchestration for MDB_ENGINE applications. Supports all MongoDB index types including regular, TTL, partial, text, geospatial, vector search, and Atlas Search indexes.

## Features

- **Index Orchestration**: High-level functions for creating indexes from manifest definitions
- **All Index Types**: Support for regular, TTL, partial, text, geospatial, vector, search, and hybrid indexes
- **Index Validation**: Built-in validation for index definitions
- **Helper Functions**: Utilities for key normalization and index comparison
- **Manifest Integration**: Seamless integration with manifest.json index definitions

## Installation

The indexes module is part of MDB_ENGINE. No additional installation required.

## Quick Start

### Basic Usage

```python
from mdb_engine.indexes import run_index_creation_for_collection
from motor.motor_asyncio import AsyncIOMotorDatabase

# Define indexes in manifest or programmatically
index_definitions = [
    {
        "name": "email_idx",
        "type": "regular",
        "keys": {"email": 1},
        "unique": True
    },
    {
        "name": "status_created_idx",
        "type": "regular",
        "keys": {"status": 1, "created_at": -1}
    }
]

# Create indexes for a collection
await run_index_creation_for_collection(
    db=db,
    slug="my_app",
    collection_name="users",
    index_definitions=index_definitions
)
```

## Index Types

### Regular Indexes

Standard MongoDB indexes for efficient queries:

```python
index_definitions = [
    {
        "name": "email_idx",
        "type": "regular",
        "keys": {"email": 1},
        "options": {
            "unique": True,
            "sparse": False
        }
    },
    {
        "name": "compound_idx",
        "type": "regular",
        "keys": {"status": 1, "created_at": -1, "priority": 1}
    }
]
```

### TTL Indexes

Time-to-live indexes that automatically expire documents:

```python
index_definitions = [
    {
        "name": "expire_sessions",
        "type": "ttl",
        "keys": {"last_activity": 1},
        "options": {
            "expireAfterSeconds": 3600  # Expire after 1 hour
        }
    }
]
```

**Note**: `expireAfterSeconds` must be at least 1 second and is required for TTL indexes.

### Partial Indexes

Indexes that only index documents matching a filter expression:

```python
index_definitions = [
    {
        "name": "active_users_idx",
        "type": "partial",
        "keys": {"email": 1},
        "options": {
            "partialFilterExpression": {
                "status": "active"
            }
        }
    }
]
```

### Text Indexes

Full-text search indexes:

```python
index_definitions = [
    {
        "name": "text_search",
        "type": "text",
        "keys": [
            ("title", "text"),
            ("content", "text"),
            ("tags", "text")
        ],
        "options": {
            "default_language": "english",
            "weights": {
                "title": 10,
                "content": 5,
                "tags": 2
            }
        }
    }
]
```

### Geospatial Indexes

Indexes for geospatial queries (2dsphere, 2d, geoHaystack):

```python
index_definitions = [
    {
        "name": "location_idx",
        "type": "geospatial",
        "keys": {"location": "2dsphere"}
    },
    {
        "name": "coordinates_idx",
        "type": "geospatial",
        "keys": {"coordinates": "2d"}
    }
]
```

### Vector Search Indexes

Atlas Vector Search indexes for semantic search:

```python
index_definitions = [
    {
        "name": "vector_search",
        "type": "vector_search",
        "definition": {
            "fields": [{
                "type": "vector",
                "path": "embedding",
                "numDimensions": 1536,
                "similarity": "cosine"
            }]
        }
    }
]
```

### Atlas Search Indexes

Lucene-based full-text search indexes:

```python
index_definitions = [
    {
        "name": "atlas_search",
        "type": "search",
        "definition": {
            "mappings": {
                "dynamic": False,
                "fields": {
                    "title": {
                        "type": "string",
                        "analyzer": "lucene.standard"
                    },
                    "content": {
                        "type": "string",
                        "analyzer": "lucene.english"
                    }
                }
            }
        }
    }
]
```

### Hybrid Indexes

Combines vector and keyword search:

```python
index_definitions = [
    {
        "name": "hybrid_search",
        "type": "hybrid",
        "definition": {
            "fields": [{
                "type": "vector",
                "path": "embedding",
                "numDimensions": 1536
            }],
            "mappings": {
                "dynamic": False,
                "fields": {
                    "title": {"type": "string"}
                }
            }
        }
    }
]
```

## Manifest Integration

Define indexes in your `manifest.json`:

```json
{
  "slug": "my_app",
  "collections": {
    "users": {
      "indexes": [
        {
          "name": "email_idx",
          "type": "regular",
          "keys": {"email": 1},
          "unique": true
        },
        {
          "name": "expire_sessions",
          "type": "ttl",
          "keys": {"last_activity": 1},
          "options": {
            "expireAfterSeconds": 3600
          }
        },
        {
          "name": "vector_search",
          "type": "vector_search",
          "definition": {
            "fields": [{
              "type": "vector",
              "path": "embedding",
              "numDimensions": 1536,
              "similarity": "cosine"
            }]
          }
        }
      ]
    }
  }
}
```

Indexes are automatically created when you register the app:

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)  # Indexes created automatically
```

## Helper Functions

### Key Normalization

Normalize index keys to consistent format:

```python
from mdb_engine.indexes.helpers import normalize_keys, keys_to_dict

# Normalize dict to list of tuples
keys_dict = {"email": 1, "name": -1}
keys_list = normalize_keys(keys_dict)
# Returns: [("email", 1), ("name", -1)]

# Convert list to dict
keys_dict = keys_to_dict(keys_list)
# Returns: {"email": 1, "name": -1}
```

### Index Validation

```python
from mdb_engine.indexes.helpers import (
    is_id_index,
    validate_index_definition_basic,
    check_and_update_index
)

# Check if index is _id index (MongoDB creates these automatically)
is_id = is_id_index({"_id": 1})  # Returns True

# Validate index definition
is_valid, error_msg = validate_index_definition_basic(
    index_def={"name": "test", "keys": {"email": 1}},
    index_name="test",
    required_fields=["keys"],
    log_prefix="[my_app]"
)

# Check if index exists and matches
exists, existing = await check_and_update_index(
    index_manager=index_manager,
    index_name="email_idx",
    expected_keys={"email": 1},
    expected_options={"unique": True}
)
```

## API Reference

### `run_index_creation_for_collection`

Main function for creating indexes from definitions:

```python
await run_index_creation_for_collection(
    db: AsyncIOMotorDatabase,
    slug: str,
    collection_name: str,
    index_definitions: List[Dict[str, Any]]
)
```

**Parameters:**
- `db`: MongoDB database instance
- `slug`: App slug (for logging)
- `collection_name`: Name of the collection
- `index_definitions`: List of index definition dictionaries

**Index Definition Format:**

```python
{
    "name": str,              # Index name (required)
    "type": str,              # Index type (required)
    "keys": dict | list,      # Index keys (required for most types)
    "options": dict,          # Index options (optional)
    "definition": dict       # Full definition (for vector/search indexes)
}
```

### Helper Functions

#### `normalize_keys(keys)`

Normalize index keys to list of tuples format.

**Parameters:**
- `keys`: Dict or list of tuples

**Returns:** List of (field_name, direction) tuples

#### `keys_to_dict(keys)`

Convert index keys to dictionary format.

**Parameters:**
- `keys`: Dict or list of tuples

**Returns:** Dictionary representation

#### `is_id_index(keys)`

Check if index keys target the `_id` field.

**Parameters:**
- `keys`: Index keys to check

**Returns:** True if this is an `_id` index

#### `check_and_update_index(index_manager, index_name, expected_keys, expected_options=None)`

Check if index exists and matches expected definition.

**Parameters:**
- `index_manager`: AsyncAtlasIndexManager instance
- `index_name`: Name of the index
- `expected_keys`: Expected index keys
- `expected_options`: Expected index options

**Returns:** Tuple of (index_exists, existing_index_dict or None)

#### `validate_index_definition_basic(index_def, index_name, required_fields, log_prefix="")`

Validate basic index definition structure.

**Parameters:**
- `index_def`: Index definition dictionary
- `index_name`: Name of the index
- `required_fields`: List of required field names
- `log_prefix`: Logging prefix

**Returns:** Tuple of (is_valid, error_message)

## Index Definition Examples

### Compound Index

```python
{
    "name": "user_status_created",
    "type": "regular",
    "keys": {
        "user_id": 1,
        "status": 1,
        "created_at": -1
    },
    "options": {
        "background": True
    }
}
```

### Unique Index with Sparse

```python
{
    "name": "username_unique",
    "type": "regular",
    "keys": {"username": 1},
    "options": {
        "unique": True,
        "sparse": True  # Only index documents with username field
    }
}
```

### TTL Index with Minimum Expiry

```python
{
    "name": "session_expiry",
    "type": "ttl",
    "keys": {"last_seen": 1},
    "options": {
        "expireAfterSeconds": 86400  # 24 hours (minimum: 1 second)
    }
}
```

### Partial Index for Active Documents

```python
{
    "name": "active_docs_idx",
    "type": "partial",
    "keys": {"title": 1, "updated_at": -1},
    "options": {
        "partialFilterExpression": {
            "status": {"$in": ["active", "published"]},
            "deleted": False
        }
    }
}
```

### Multi-Field Text Index

```python
{
    "name": "full_text_search",
    "type": "text",
    "keys": [
        ("title", "text"),
        ("description", "text"),
        ("tags", "text")
    ],
    "options": {
        "default_language": "english",
        "weights": {
            "title": 10,
            "description": 5,
            "tags": 2
        }
    }
}
```

### Vector Search Index

```python
{
    "name": "embeddings_vector",
    "type": "vector_search",
    "definition": {
        "fields": [{
            "type": "vector",
            "path": "embedding",
            "numDimensions": 1536,
            "similarity": "cosine"
        }]
    }
}
```

### Atlas Search Index

```python
{
    "name": "atlas_fulltext",
    "type": "search",
    "definition": {
        "mappings": {
            "dynamic": False,
            "fields": {
                "title": {
                    "type": "string",
                    "analyzer": "lucene.standard"
                },
                "content": {
                    "type": "string",
                    "analyzer": "lucene.english"
                },
                "tags": {
                    "type": "stringFacet"
                }
            }
        }
    }
}
```

## Best Practices

1. **Name indexes descriptively** - Use clear, descriptive names (e.g., `user_email_unique_idx`)
2. **Define indexes in manifest** - Centralize index definitions in manifest.json
3. **Use compound indexes wisely** - Order fields by selectivity (most selective first)
4. **Consider partial indexes** - Use partial indexes to reduce index size
5. **Set TTL appropriately** - Use TTL indexes for time-based data expiration
6. **Validate definitions** - Use validation functions before creating indexes
7. **Monitor index creation** - Check logs for index creation status
8. **Use background creation** - Set `background: True` for large collections
9. **Test index performance** - Verify indexes improve query performance
10. **Document index purpose** - Add comments explaining why each index exists

## Error Handling

```python
from mdb_engine.indexes import run_index_creation_for_collection
import logging

logger = logging.getLogger(__name__)

try:
    await run_index_creation_for_collection(
        db=db,
        slug="my_app",
        collection_name="users",
        index_definitions=index_definitions
    )
except (PyMongoError, ValueError, TypeError) as e:
    logger.error(f"Failed to create indexes: {e}", exc_info=True)
    raise
```

## Integration Examples

### MongoDBEngine Integration

Indexes are automatically created when registering apps:

```python
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

# Load manifest with index definitions
manifest = await engine.load_manifest("manifest.json")

# Register app (indexes created automatically)
await engine.register_app(manifest)
```

### Manual Index Creation

Create indexes programmatically:

```python
from mdb_engine.indexes import run_index_creation_for_collection

index_definitions = [
    {
        "name": "email_idx",
        "type": "regular",
        "keys": {"email": 1},
        "unique": True
    }
]

# Get scoped database and collection
db = engine.get_scoped_db("my_app")
collection = db.users

# Use collection.index_manager for index operations
index_manager = collection.index_manager
for index_def in index_definitions:
    if index_def["type"] == "regular":
        await index_manager.create_index(
            keys=list(index_def["keys"].items()),
            name=index_def.get("name"),
            **index_def.get("options", {})
        )
    # Handle other index types similarly...
```

### Multiple Collections

Create indexes for multiple collections:

```python
collections_with_indexes = {
    "users": [
        {"name": "email_idx", "type": "regular", "keys": {"email": 1}, "unique": True}
    ],
    "documents": [
        {"name": "vector_idx", "type": "vector_search", "definition": {...}}
    ]
}

# Get scoped database
db = engine.get_scoped_db("my_app")

# Use collection.index_manager for each collection
for collection_name, index_definitions in collections_with_indexes.items():
    collection = getattr(db, collection_name)
    index_manager = collection.index_manager
    
    for index_def in index_definitions:
        if index_def["type"] == "regular":
            await index_manager.create_index(
                keys=list(index_def["keys"].items()),
                name=index_def.get("name"),
                **index_def.get("options", {})
            )
        # Handle other index types similarly...
```

## Related Modules

- **`database/`** - AsyncAtlasIndexManager for Atlas indexes
- **`core/`** - Manifest system and MongoDBEngine
- **`observability/`** - Logging for index operations
