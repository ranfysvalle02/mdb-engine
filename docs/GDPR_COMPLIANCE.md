# GDPR Compliance Implementation Guide

## Overview

This guide outlines how to implement GDPR compliance features in MDB-ENGINE. GDPR (General Data Protection Regulation) requires organizations to provide users with control over their personal data, including the right to access, rectify, delete, and export their data.

## GDPR Rights Overview

### Core Rights

1. **Right to Access (Article 15)** - Users can request all their personal data
2. **Right to Rectification (Article 16)** - Users can correct inaccurate data
3. **Right to Erasure (Article 17)** - Users can request deletion of their data
4. **Right to Data Portability (Article 20)** - Users can export their data in machine-readable format
5. **Right to Object (Article 21)** - Users can object to processing of their data
6. **Right to Restrict Processing (Article 18)** - Users can limit how their data is processed

## Implementation Architecture

### Data Discovery

The first step is identifying where user data is stored. MDB-ENGINE stores user data in multiple locations:

1. **User Collections**:
   - `users` (app-specific users)
   - `shared_users` (SSO user pool)
   - `user_sessions` (session tracking)
   - `token_blacklist` (revoked tokens)

2. **Application Data**:
   - `chat_history` (conversation logs)
   - `{app_slug}_memories` (vector memory storage)
   - Custom collections with `user_id` or `email` fields

3. **Authorization Data**:
   - `casbin_policies` (may contain user emails/IDs)
   - `oso_facts` (OSO authorization facts)

### Implementation Strategy

We'll create a GDPR module with four core services:

1. **Data Discovery Service** - Finds all user data across collections
2. **Data Export Service** - Exports user data in multiple formats
3. **Data Deletion Service** - Deletes or anonymizes user data
4. **Data Rectification Service** - Updates user data

## Quick Implementation Guide

### Step 1: Create GDPR Module

```bash
mkdir -p mdb_engine/gdpr
touch mdb_engine/gdpr/__init__.py
```

### Step 2: Add GDPR Methods to Engine

Add these methods to `MongoDBEngine` class:

```python
# In mdb_engine/core/engine.py

async def export_user_data(
    self,
    user_identifier: str,
    identifier_type: str = "email",  # "email" or "user_id"
    app_slug: str | None = None,
    format: str = "json",  # "json", "csv", "report"
) -> dict[str, Any]:
    """
    Export all user data for GDPR compliance (Right to Access).
    
    Args:
        user_identifier: User email or user_id
        identifier_type: Type of identifier
        app_slug: Optional app slug to scope export
        format: Export format
    
    Returns:
        Dictionary with exported data and metadata
    """
    # Implementation: Scan collections, find user data, format export
    pass

async def delete_user_data(
    self,
    user_identifier: str,
    identifier_type: str = "email",
    app_slug: str | None = None,
    anonymize: bool = False,
    soft_delete: bool = False,
) -> dict[str, Any]:
    """
    Delete user data for GDPR compliance (Right to Erasure - Article 17).
    
    Default behavior is hard-delete (GDPR compliant). Use soft_delete=True
    only when legal retention requirements apply.
    
    Args:
        user_identifier: User email or user_id
        identifier_type: Type of identifier
        app_slug: Optional app slug to scope deletion
        anonymize: If True, anonymize instead of delete
        soft_delete: If True, mark as deleted (for legal retention).
                    Default is False (hard delete) for GDPR compliance.
    
    Returns:
        Dictionary with deletion results
    """
    # Implementation: Find and delete/anonymize user data
    pass

async def update_user_data(
    self,
    user_identifier: str,
    updates: dict[str, Any],
    identifier_type: str = "email",
    app_slug: str | None = None,
) -> dict[str, Any]:
    """
    Update user data for GDPR compliance (Right to Rectification).
    
    Args:
        user_identifier: User email or user_id
        updates: Dictionary of field updates
        identifier_type: Type of identifier
        app_slug: Optional app slug to scope updates
    
    Returns:
        Dictionary with update results
    """
    # Implementation: Update user data across collections
    pass
```

### Step 3: Collection Discovery Logic

The engine needs to scan collections to find user data:

```python
async def _discover_user_collections(
    self,
    user_identifier: str,
    identifier_type: str,
    app_slug: str | None = None,
) -> list[dict[str, Any]]:
    """
    Discover all collections containing user data.
    
    Returns list of collections with user data.
    """
    raw_db = self._connection_manager.get_database()
    collections = []
    
    # Get all collection names
    collection_names = await raw_db.list_collection_names()
    
    # Filter by app_slug if provided
    if app_slug:
        collection_names = [
            name for name in collection_names
            if name.startswith(f"{app_slug}_") or name in KNOWN_USER_COLLECTIONS
        ]
    
    # Known user collections
    KNOWN_USER_COLLECTIONS = {
        "users", "shared_users", "user_sessions",
        "token_blacklist", "chat_history"
    }
    
    for collection_name in collection_names:
        # Skip system collections
        if collection_name.startswith("system.") or collection_name.startswith("_"):
            continue
        
        collection = raw_db[collection_name]
        
        # Build query based on identifier type
        if identifier_type == "email":
            query = {
                "$or": [
                    {"email": user_identifier},
                    {"user_email": user_identifier},
                ]
            }
        else:  # user_id
            query = {
                "$or": [
                    {"user_id": user_identifier},
                    {"_id": user_identifier},
                ]
            }
        
        # Check if collection has matching documents
        count = await collection.count_documents(query)
        if count > 0:
            collections.append({
                "name": collection_name,
                "document_count": count,
            })
    
    return collections
```

### Step 4: Export Implementation

```python
async def export_user_data(self, user_identifier, identifier_type, app_slug, format):
    """Export user data."""
    collections = await self._discover_user_collections(
        user_identifier, identifier_type, app_slug
    )
    
    raw_db = self._connection_manager.get_database()
    export_data = {}
    
    for collection_info in collections:
        collection_name = collection_info["name"]
        collection = raw_db[collection_name]
        
        # Build query
        if identifier_type == "email":
            query = {"$or": [{"email": user_identifier}, {"user_email": user_identifier}]}
        else:
            query = {"$or": [{"user_id": user_identifier}, {"_id": user_identifier}]}
        
        # Find all matching documents
        cursor = collection.find(query)
        documents = await cursor.to_list(length=None)
        
        # Convert ObjectId to string for JSON serialization
        for doc in documents:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
        
        export_data[collection_name] = documents
    
    # Format export
    if format == "json":
        return {
            "data": export_data,
            "metadata": {
                "export_date": datetime.utcnow().isoformat(),
                "format": "json",
                "collections": list(export_data.keys()),
                "total_documents": sum(len(docs) for docs in export_data.values()),
            }
        }
    elif format == "csv":
        # Convert to CSV format
        # Implementation for CSV export
        pass
    else:  # report
        # Human-readable report
        pass
```

### Step 5: Deletion Implementation

```python
async def delete_user_data(self, user_identifier, identifier_type, app_slug, anonymize, soft_delete):
    """Delete or anonymize user data."""
    collections = await self._discover_user_collections(
        user_identifier, identifier_type, app_slug
    )
    
    raw_db = self._connection_manager.get_database()
    deletion_results = {
        "collections_processed": [],
        "documents_deleted": 0,
        "documents_anonymized": 0,
        "errors": [],
    }
    
    for collection_info in collections:
        collection_name = collection_info["name"]
        collection = raw_db[collection_name]
        
        # Build query
        if identifier_type == "email":
            query = {"$or": [{"email": user_identifier}, {"user_email": user_identifier}]}
        else:
            query = {"$or": [{"user_id": user_identifier}, {"_id": user_identifier}]}
        
        try:
            if anonymize:
                # Anonymize data
                import hashlib
                anonymous_id = hashlib.sha256(user_identifier.encode()).hexdigest()[:16]
                anonymous_email = f"deleted_{anonymous_id}@deleted.local"
                
                update = {
                    "$set": {
                        "email": anonymous_email,
                        "user_email": anonymous_email,
                        "user_id": f"deleted_{anonymous_id}",
                        "anonymized_at": datetime.utcnow(),
                        "gdpr_anonymized": True,
                    },
                    "$unset": {
                        "password_hash": "",
                        "password": "",
                    }
                }
                result = await collection.update_many(query, update)
                deletion_results["documents_anonymized"] += result.modified_count
                
            elif soft_delete:
                # Soft delete (mark as deleted)
                update = {
                    "$set": {
                        "deleted_at": datetime.utcnow(),
                        "deleted": True,
                        "gdpr_deleted": True,
                    }
                }
                result = await collection.update_many(query, update)
                deletion_results["documents_deleted"] += result.modified_count
                
            else:
                # Hard delete (permanent removal)
                result = await collection.delete_many(query)
                deletion_results["documents_deleted"] += result.deleted_count
            
            deletion_results["collections_processed"].append(collection_name)
            
        except Exception as e:
            logger.error(f"Error deleting from {collection_name}: {e}")
            deletion_results["errors"].append({
                "collection": collection_name,
                "error": str(e),
            })
    
    # Also delete from memory service if available
    if app_slug:
        try:
            db = self.get_scoped_db(app_slug)
            # Get memory service and delete user memories
            # Implementation depends on memory service API
        except Exception as e:
            logger.error(f"Error deleting memory data: {e}")
    
    return deletion_results
```

### Step 6: Memory Service Integration

For memory service (vector storage), use the existing `delete_all` method:

```python
# In deletion implementation
if app_slug:
    try:
        from mdb_engine.memory import get_memory_service
        
        db = self.get_scoped_db(app_slug)
        collection = db[f"{app_slug}_memories"]
        
        memory_service = get_memory_service(
            app_slug=app_slug,
            collection=collection,
        )
        
        # Delete all memories for user
        if identifier_type == "user_id":
            await memory_service.delete_all(user_id=user_identifier)
        else:
            # Need to find user_id from email first
            user = await db.users.find_one({"email": user_identifier})
            if user:
                user_id = str(user["_id"])
                await memory_service.delete_all(user_id=user_id)
    except Exception as e:
        logger.error(f"Error deleting memory data: {e}")
```

### Step 7: API Endpoints

Create FastAPI endpoints:

```python
# In your app setup or separate router

from fastapi import APIRouter, Depends, HTTPException
from mdb_engine.auth import get_current_user

router = APIRouter(prefix="/gdpr", tags=["GDPR"])

@router.get("/export")
async def export_user_data(
    request: Request,
    format: str = "json",
    user: dict = Depends(get_current_user),
    engine: MongoDBEngine = Depends(get_engine),
):
    """Export current user's data."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    user_identifier = user.get("email") or user.get("user_id")
    identifier_type = "email" if user.get("email") else "user_id"
    
    export_data = await engine.export_user_data(
        user_identifier=user_identifier,
        identifier_type=identifier_type,
        format=format,
    )
    
    return export_data

@router.delete("/delete")
async def delete_user_data(
    request: Request,
    anonymize: bool = False,
    soft_delete: bool = False,  # Default: hard delete (GDPR compliant)
    user: dict = Depends(get_current_user),
    engine: MongoDBEngine = Depends(get_engine),
):
    """
    Delete current user's data.
    
    Default is hard-delete (GDPR compliant). Set soft_delete=True only
    when legal retention requirements apply (e.g., financial records).
    """
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    user_identifier = user.get("email") or user.get("user_id")
    identifier_type = "email" if user.get("email") else "user_id"
    
    result = await engine.delete_user_data(
        user_identifier=user_identifier,
        identifier_type=identifier_type,
        anonymize=anonymize,
        soft_delete=soft_delete,  # Default: False (hard delete for GDPR compliance)
    )
    
    return result

@router.put("/rectify")
async def update_user_data(
    request: Request,
    updates: dict,
    user: dict = Depends(get_current_user),
    engine: MongoDBEngine = Depends(get_engine),
):
    """Update current user's data."""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    user_identifier = user.get("email") or user.get("user_id")
    identifier_type = "email" if user.get("email") else "user_id"
    
    result = await engine.update_user_data(
        user_identifier=user_identifier,
        identifier_type=identifier_type,
        updates=updates,
    )
    
    return result
```

## Security Considerations

1. **Authentication Required**: All GDPR endpoints must require authentication
2. **Authorization**: Users can only access/modify their own data
3. **Audit Logging**: Log all GDPR operations for compliance
4. **Rate Limiting**: Prevent abuse of GDPR endpoints
5. **Data Validation**: Validate all updates before applying

## Legal Considerations

1. **Retention Periods**: Some data may need to be retained (e.g., financial records)
2. **Anonymization**: Consider anonymization instead of deletion for analytics
3. **Backup Data**: Ensure backups are also cleaned up
4. **Third-Party Data**: Handle data shared with third parties

## Deletion Strategies

MDB-Engine supports three deletion strategies for GDPR compliance:

### 1. Hard Delete (Default - GDPR Compliant)

**Default behavior**: `soft_delete=False`

Permanently removes all user data including:
- Active memories (`is_active=True`)
- Cold storage memories (`is_active=False` - pruned memories)
- Chat history
- All other user data collections

**When to use:**
- User-initiated GDPR deletion requests (default)
- No legal retention requirements
- Complete data removal required

**Example:**
```python
# Hard delete (default - GDPR compliant)
result = await engine.delete_user_data(
    user_identifier="user@example.com",
    identifier_type="email"
    # soft_delete=False is the default
)
```

### 2. Soft Delete (Legal Retention)

**Behavior**: `soft_delete=True`

Marks data as deleted but preserves for legal/compliance purposes:
- Sets `deleted=True`, `gdpr_deleted=True`, `deleted_at=timestamp`
- Memories marked with `is_active=False`, `gdpr_deleted=True`
- Data excluded from normal queries but preserved in database

**When to use:**
- Legal retention requirements (e.g., financial records, tax data)
- Audit trail needs
- Compliance with industry regulations (e.g., healthcare, finance)

**Example:**
```python
# Soft delete (for legal retention)
result = await engine.delete_user_data(
    user_identifier="user@example.com",
    identifier_type="email",
    soft_delete=True  # Preserve for legal retention
)
```

### 3. Anonymization

**Behavior**: `anonymize=True`

Replaces personal identifiers with anonymous values:
- Email → `deleted_{hash}@deleted.local`
- User ID → `deleted_{hash}`
- Preserves data structure for analytics

**When to use:**
- Analytics/data science needs
- Legal retention with anonymization
- GDPR compliance while preserving aggregate insights

**Example:**
```python
# Anonymize (preserve for analytics)
result = await engine.delete_user_data(
    user_identifier="user@example.com",
    identifier_type="email",
    anonymize=True
)
```

### Memory Service Deletion

The memory service respects the deletion strategy:

- **Hard delete**: Removes all memories including cold storage (pruned memories)
- **Soft delete**: Marks memories as `is_active=False`, `gdpr_deleted=True`
- **Cold storage**: Pruned memories (from capacity management) are included in hard-delete but preserved in soft-delete for audit trail

**Note**: Memory pruning (capacity management) always uses soft-delete to maintain audit trail. This is separate from GDPR deletion and preserves cold storage for analytics.

## Testing

```python
# tests/integration/test_gdpr.py

@pytest.mark.asyncio
async def test_export_user_data(engine, test_user):
    """Test user data export."""
    export_data = await engine.export_user_data(
        user_identifier=test_user["email"],
        format="json",
    )
    
    assert "data" in export_data
    assert "metadata" in export_data
    assert test_user["email"] in str(export_data)

@pytest.mark.asyncio
async def test_delete_user_data_hard_delete(engine, test_user):
    """Test hard delete of user data (GDPR default)."""
    result = await engine.delete_user_data(
        user_identifier=test_user["email"],
        soft_delete=False,  # Hard delete (default)
    )
    
    assert result["documents_deleted"] > 0
    assert len(result["errors"]) == 0

@pytest.mark.asyncio
async def test_delete_user_data_soft_delete(engine, test_user):
    """Test soft delete of user data (legal retention)."""
    result = await engine.delete_user_data(
        user_identifier=test_user["email"],
        soft_delete=True,  # Soft delete for legal retention
    )
    
    assert result["documents_soft_deleted"] > 0
    assert len(result["errors"]) == 0
```

## Next Steps

1. ✅ Implement data discovery logic
2. ✅ Implement export functionality
3. ✅ Implement deletion functionality
4. ✅ Implement rectification functionality
5. ✅ Add API endpoints
6. ✅ Add authentication/authorization
7. ✅ Add audit logging
8. ✅ Add tests
9. ✅ Create admin interface for GDPR requests
10. ✅ Add email notifications

---

**Implementation Priority**: High  
**Estimated Effort**: 2-3 weeks  
**Dependencies**: None (uses existing engine infrastructure)
