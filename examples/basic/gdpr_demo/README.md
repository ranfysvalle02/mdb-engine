# GDPR Compliance Demo

This example demonstrates GDPR compliance features in MDB-Engine, including:

- **Right to Access (Article 15)**: Export all user data
- **Right to Erasure (Article 17)**: Delete user data with hard/soft delete strategies
- **Right to Rectification (Article 16)**: Update user data
- **Memory Service Integration**: Shows how memories are handled in GDPR deletion

## Features

### 1. Data Export
- Export all user data in JSON format
- Includes memories, chat history, user profile, and all related data
- Endpoint: `GET /api/gdpr/export`

### 2. Data Deletion
- **Hard Delete (Default)**: Permanently removes all data including cold storage (GDPR compliant)
- **Soft Delete**: Marks data as deleted for legal retention requirements
- **Anonymization**: Replaces identifiers with anonymous values
- Endpoint: `DELETE /api/gdpr/delete?soft_delete=false`

### 3. Data Rectification
- Update user profile information
- Endpoint: `PUT /api/gdpr/rectify`

### 4. Memory Management
- Add memories for demonstration
- View all memories
- Delete all memories (with required `hard_delete` parameter)
- Demonstrates the new GDPR-compliant deletion strategy

## Deletion Strategies

### Hard Delete (Default - GDPR Compliant)
```python
# Permanently removes all data
DELETE /api/gdpr/delete?soft_delete=false
```

**What gets deleted:**
- All active memories (`is_active=True`)
- All cold storage memories (`is_active=False` - pruned memories)
- Chat history
- User profile
- All other user data

### Soft Delete (Legal Retention)
```python
# Marks as deleted but preserves for legal retention
DELETE /api/gdpr/delete?soft_delete=true
```

**What happens:**
- Data marked with `deleted=True`, `gdpr_deleted=True`, `deleted_at=timestamp`
- Memories marked with `is_active=False`, `gdpr_deleted=True`
- Data excluded from normal queries but preserved in database
- Use when legal retention requirements apply (e.g., financial records, tax data)

### Anonymization
```python
# Replaces identifiers with anonymous values
DELETE /api/gdpr/delete?anonymize=true
```

**What happens:**
- Email → `deleted_{hash}@deleted.local`
- User ID → `deleted_{hash}`
- Preserves data structure for analytics

## Memory Service Deletion

The memory service now requires explicit `hard_delete` parameter:

```python
# Hard delete (permanently remove)
memory_service.delete_all(user_id=user_id, hard_delete=True)

# Soft delete (mark as deleted)
memory_service.delete_all(user_id=user_id, hard_delete=False)
```

**Note**: Memory pruning (capacity management) always uses soft-delete to maintain audit trail. This is separate from GDPR deletion.

## Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Set environment variables:**
```bash
export MDB_MONGO_URI="mongodb://localhost:27017/"
export MDB_DB_NAME="gdpr_demo_db"
export OPENAI_API_KEY="your-openai-api-key"  # For memory service
```

3. **Run the application:**
```bash
python web.py
```

Or with Docker:
```bash
docker-compose up
```

4. **Access the application:**
- Open http://localhost:8000
- Register a new account or login
- Navigate to the GDPR dashboard

## API Endpoints

### Authentication
- `GET /login` - Login page
- `POST /login` - Handle login
- `GET /register` - Registration page
- `POST /register` - Handle registration
- `GET /logout` - Logout

### GDPR Endpoints
- `GET /api/gdpr/export` - Export all user data
- `DELETE /api/gdpr/delete?soft_delete=false` - Delete user data (hard delete)
- `DELETE /api/gdpr/delete?soft_delete=true` - Delete user data (soft delete)
- `PUT /api/gdpr/rectify` - Update user data

### Memory Management
- `POST /api/memories/add` - Add a memory
- `GET /api/memories` - Get all memories
- `DELETE /api/memories?hard_delete=true` - Delete all memories (hard delete)
- `DELETE /api/memories?hard_delete=false` - Delete all memories (soft delete)

## Example Usage

### Export User Data
```bash
curl -X GET "http://localhost:8000/api/gdpr/export" \
  -H "Cookie: gdpr_demo_session=your-session-id"
```

### Hard Delete (GDPR Compliant)
```bash
curl -X DELETE "http://localhost:8000/api/gdpr/delete?soft_delete=false" \
  -H "Cookie: gdpr_demo_session=your-session-id"
```

### Soft Delete (Legal Retention)
```bash
curl -X DELETE "http://localhost:8000/api/gdpr/delete?soft_delete=true" \
  -H "Cookie: gdpr_demo_session=your-session-id"
```

### Add Memory
```bash
curl -X POST "http://localhost:8000/api/memories/add" \
  -H "Content-Type: application/json" \
  -H "Cookie: gdpr_demo_session=your-session-id" \
  -d '{"memory": "User prefers dark mode interfaces"}'
```

### Delete All Memories (Hard Delete)
```bash
curl -X DELETE "http://localhost:8000/api/memories?hard_delete=true" \
  -H "Cookie: gdpr_demo_session=your-session-id"
```

## Important Notes

1. **Hard Delete is Default**: For GDPR compliance, hard delete is the default behavior
2. **Explicit Choice Required**: Memory service requires explicit `hard_delete` parameter
3. **Cold Storage**: Pruned memories (cold storage) are included in hard delete but preserved in soft delete
4. **Memory Pruning**: Capacity management uses soft-delete (separate from GDPR deletion)
5. **Legal Retention**: Use soft delete only when legal retention requirements apply

## Testing

1. Register a new user
2. Add some memories
3. Export user data to see what's stored
4. Test hard delete (permanently removes everything)
5. Test soft delete (marks as deleted but preserves)
6. Verify memories are excluded from search after soft delete

## Related Documentation

- [GDPR Compliance Guide](../../../docs/GDPR_COMPLIANCE.md)
- [Memory Service Documentation](../../../docs/MEMORY_SERVICE.md)
- [Memory Deep Dive](../../../docs/MEMORY_DEEP_DIVE.md)
