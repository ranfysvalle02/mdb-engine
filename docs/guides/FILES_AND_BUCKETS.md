# Files and Buckets: Memory Organization Guide

## Introduction

Memory buckets are a powerful organizational system in MDB-Engine that allows you to isolate and categorize memories by context. When a user has conversations about "Work", "School", "Personal", or any custom category, memories stay isolated within their respective buckets. Files uploaded to the system can be associated with these category buckets, enabling unified search across both conversation memories and document memories.

### Why Buckets Matter

Without buckets, all memories exist in a single pool:
- Work-related memories might appear in personal conversations
- File contents could leak into unrelated contexts
- No way to scope AI context to a specific domain

With buckets:
- **Memory Isolation**: Work memories stay in work context
- **Unified Search**: Find both conversation and file memories in one query
- **Organized Context**: AI only sees relevant memories for the current bucket
- **File Association**: Documents link to their category for contextual retrieval

---

## What MDB-Engine Provides

MDB-Engine's memory service provides the infrastructure for bucket organization through **optional metadata fields**. Buckets are NOT configured in `manifest.json` - they are runtime parameters passed to the memory API.

### Core Bucket Fields

| Field | Storage Location | Purpose |
|-------|------------------|---------|
| `bucket_id` | `metadata.bucket_id` | Unique identifier for the bucket |
| `bucket_type` | `metadata.bucket_type` | Type classification (`"general"`, `"file"`, `"conversation"`) |
| `associated_bucket_id` | `metadata.associated_bucket_id` | Links file memories to category buckets (for unified search) |

### Memory Service API

The `BaseMemoryService` interface accepts bucket parameters on all memory operations:

```python
from mdb_engine.dependencies import get_memory_service

# Adding memories with bucket organization
memory_service.add(
    messages="User prefers dark mode for coding",
    user_id="user123",
    bucket_id="category:work:user123",    # Optional
    bucket_type="category",                # Optional
    metadata={"source": "chat"}
)

# Direct injection with bucket
memory_service.inject(
    memory="User is allergic to shellfish",
    user_id="user123",
    bucket_id="bucket:health:user123",
    bucket_type="general",
    metadata={"category": "health"}
)

# Search with bucket filtering
results = memory_service.search(
    query="What are the user's preferences?",
    user_id="user123",
    filters={"metadata": {"associated_bucket_id": "category:work:user123"}}
)
```

### CognitiveEngine Integration

The `CognitiveEngine` (orchestrator) provides bucket-aware chat with automatic memory filtering:

```python
from mdb_engine.memory import CognitiveEngine

# Chat with "work" context - only work memories retrieved
result = await cognitive_engine.chat(
    user_id="user123",
    session_id="conversation:456",
    user_query="What meetings do I have?",
    bucket_id="category:work:user123",     # Bucket filter
    bucket_type="category",                 # Bucket type
    extract_facts=True                      # Auto-extract new memories
)

# New memories are stored with this bucket_id
# LTM search is filtered to only return memories from this bucket
```

---

## Bucket ID Patterns

MDB-Engine doesn't enforce bucket naming conventions - these are patterns established by convention. You can create your own patterns that fit your application's needs.

### Standard Patterns

| Pattern | Description | Example |
|---------|-------------|---------|
| `category:{name}:{user_id}` | Category-based bucket | `category:work:user123` |
| `bucket:{name}:{user_id}` | Alternative category pattern | `bucket:general:user123` |
| `file:{filename}:{user_id}` | File-specific bucket | `file:report.pdf:user123` |
| `session:{id}` | Session-scoped (default for chats) | `session:conv456` |

### Pattern Guidelines

1. **Include user_id**: Ensures user isolation
2. **Use prefixes**: Makes bucket types easily identifiable
3. **Be consistent**: Pick one pattern per bucket type and stick with it
4. **Keep it readable**: Bucket IDs appear in logs and debugging

---

## File Memory Architecture

The key insight for file memory is the **dual-bucket pattern**: each file has its own unique bucket ID, but it's also associated with a category bucket for unified search.

### The Dual-Bucket Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                     Category Bucket                          │
│                 bucket:work:user123                          │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ Conversation    │  │ Conversation    │                   │
│  │ Memory 1        │  │ Memory 2        │                   │
│  │ associated_     │  │ associated_     │                   │
│  │ bucket_id:      │  │ bucket_id:      │                   │
│  │ bucket:work:u123│  │ bucket:work:u123│                   │
│  └─────────────────┘  └─────────────────┘                   │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              File: quarterly_report.pdf              │    │
│  │                                                      │    │
│  │  bucket_id: file:quarterly_report.pdf:user123       │    │
│  │  associated_bucket_id: bucket:work:user123          │    │
│  │                                                      │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐            │    │
│  │  │ Fact 1   │ │ Fact 2   │ │ Fact 3   │            │    │
│  │  │(from doc)│ │(from doc)│ │(from doc)│            │    │
│  │  └──────────┘ └──────────┘ └──────────┘            │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### How It Works

1. **File Upload**: User uploads `quarterly_report.pdf` to the "Work" category
2. **File Bucket Created**: `bucket_id = "file:quarterly_report.pdf:user123"`
3. **Category Association**: `associated_bucket_id = "bucket:work:user123"`
4. **Facts Extracted**: Document is chunked, facts extracted, each stored with both IDs
5. **Unified Search**: Query for "work" finds both conversation AND file memories

### The Unified Search Magic

When you search with `bucket_id="bucket:work:user123"`, the CognitiveEngine filters by `associated_bucket_id`:

```python
# From mdb_engine/memory/orchestrator.py
if bucket_id:
    if "metadata" not in ltm_filters:
        ltm_filters["metadata"] = {}
    # Uses associated_bucket_id to find BOTH:
    # - Conversation memories (where associated_bucket_id = bucket_id)
    # - File memories (where associated_bucket_id links to category bucket)
    ltm_filters["metadata"]["associated_bucket_id"] = bucket_id
```

This means:
- Conversation memories where `associated_bucket_id = "bucket:work:user123"` are found
- File memories where `associated_bucket_id = "bucket:work:user123"` are found
- Both appear in search results, creating unified context

---

## sso-app-3 Implementation

The `sso-app-3` example demonstrates a complete file memory implementation with category buckets.

### Manifest Configuration

Categories are configured in `manifest.json`, but buckets themselves are created at runtime:

```json
{
  "memory_config": {
    "enabled": true,
    "provider": "cognitive",
    "categories": {
      "enabled": true,
      "custom_categories": ["work", "health", "finance", "travel"]
    }
  }
}
```

### File Processing Function

The core file processing happens in `process_and_store_file_memory()`:

```python
async def process_and_store_file_memory(
    svc, user_id: str, file_data: dict, category: str, associated_bucket_id: str = None
) -> int:
    """
    Orchestrates the parallel processing of a file with enhanced metadata injection.
    """
    filename = file_data["filename"]
    raw_text = file_data["raw_text"]

    # 1. Global Metadata Extraction (First Pass)
    doc_metadata = await extract_global_metadata(raw_text, filename)

    # 2. Create Bucket IDs
    file_bucket_id = f"file:{filename}:{user_id}"
    cat_bucket_id = associated_bucket_id or (
        f"bucket:{category}:{user_id}" if category != "general" else f"bucket:general:{user_id}"
    )

    # 3. Store Raw Content with Rich Metadata
    if raw_content_service:
        await raw_content_service.store_raw_content(
            raw_content=raw_text,
            user_id=user_id,
            bucket_id=file_bucket_id,
            metadata={
                "filename": filename,
                "associated_bucket_id": cat_bucket_id,  # Links to category!
                "category": category,
                "title": doc_metadata.title,
                "author": doc_metadata.author,
                "organization": doc_metadata.organization,
            },
        )

    # 4. Parallel Fact Extraction
    chunks = semantic_chunking(raw_text)
    
    # 5. Store each fact with bucket association
    for chunk in chunks:
        facts = await extract_facts_from_chunk(chunk)
        for fact in facts:
            await svc.inject(
                memory=fact,
                user_id=user_id,
                bucket_id=file_bucket_id,
                bucket_type="file",
                metadata={
                    "filename": filename,
                    "associated_bucket_id": cat_bucket_id,
                    "category": category,
                    "source": "document",
                },
            )
    
    return len(facts)
```

### API Endpoints for File Buckets

sso-app-3 provides REST endpoints for managing files within buckets:

#### GET `/api/buckets/{bucket_id}/files`

Retrieve all files associated with a bucket:

```python
@app.get("/api/buckets/{bucket_id}/files", response_class=JSONResponse)
async def get_bucket_files(
    request: Request,
    bucket_id: str,
    svc=Depends(get_memory_service),
):
    """Get all files associated with a bucket."""
    user = get_current_user(request)
    user_id = str(user["_id"])
    
    # Find all memories with this bucket as associated_bucket_id
    all_mems = await asyncio.to_thread(svc.get_all, user_id=user_id, limit=2000)
    
    files_list = []
    seen = set()
    
    for m in all_mems:
        meta = m.get("metadata", {})
        assoc = meta.get("associated_bucket_id")
        
        # Check if this memory is associated with the requested bucket
        if assoc == bucket_id:
            f_bucket = meta.get("bucket_id", "")
            if f_bucket.startswith("file:") and f_bucket not in seen:
                files_list.append({
                    "bucket_id": f_bucket,
                    "filename": meta.get("filename"),
                    "memory_count": 1,  # Would aggregate in production
                })
                seen.add(f_bucket)
    
    return JSONResponse({"success": True, "files": files_list})
```

#### POST `/api/buckets/{bucket_id}/files`

Add files to a bucket:

```python
@app.post("/api/buckets/{bucket_id}/files", response_class=JSONResponse)
async def add_file_to_bucket(
    request: Request,
    bucket_id: str,
    files: list[UploadFile] = File(default=[]),
    svc=Depends(get_memory_service),
):
    """Add files to a bucket and process them for memory storage."""
    user = get_current_user(request)
    user_id = str(user["_id"])
    
    # Extract category from bucket_id (e.g., "bucket:work:user123" -> "work")
    parts = bucket_id.split(":")
    category = parts[1] if len(parts) >= 2 else "general"
    
    results = []
    for f in files:
        # Convert file to text
        data = await convert_file_to_markdown(f)
        
        if data["raw_text"]:
            # Process and store with bucket association
            count = await process_and_store_file_memory(
                svc=svc,
                user_id=user_id,
                file_data=data,
                category=category,
                associated_bucket_id=bucket_id,  # Link to this bucket!
            )
            results.append({
                "filename": data["filename"],
                "memories_created": count,
            })
    
    return JSONResponse({"success": True, "results": results})
```

### Memory Stats with Bucket Files

The `/api/memories/stats` endpoint aggregates file information per bucket:

```python
@app.get("/api/memories/stats", response_class=JSONResponse)
async def get_memory_stats(request: Request, svc=Depends(get_memory_service)):
    """Get memory statistics including bucket files."""
    user = get_current_user(request)
    user_id = str(user["_id"])
    
    all_mems = await asyncio.to_thread(svc.get_all, user_id=user_id, limit=2000)
    stats = {
        "file_contexts": {},
        "general_buckets": {},
        "bucket_files": {}  # Files organized by their associated bucket
    }
    
    for m in all_mems:
        meta = m.get("metadata", {})
        bid = meta.get("bucket_id")
        
        if bid and bid.startswith("file:"):
            # This is a file memory
            fname = meta.get("filename", "Unknown")
            stats["file_contexts"][fname] = {
                "context_id": bid,
                "count": stats["file_contexts"].get(fname, {}).get("count", 0) + 1,
            }
            
            # Track which bucket this file is associated with
            assoc = meta.get("associated_bucket_id")
            if assoc:
                if assoc not in stats["bucket_files"]:
                    stats["bucket_files"][assoc] = {}
                stats["bucket_files"][assoc][bid] = {
                    "filename": fname,
                    "bucket_id": bid,
                    "memory_count": 1,
                }
        else:
            # General bucket memory
            cat = meta.get("category", "General")
            if bid:
                stats["general_buckets"][bid] = {
                    "name": cat,
                    "count": stats["general_buckets"].get(bid, {}).get("count", 0) + 1,
                }
    
    # Convert bucket_files to list format
    stats["bucket_files"] = {k: list(v.values()) for k, v in stats["bucket_files"].items()}
    
    return JSONResponse({"success": True, "stats": stats})
```

---

## Creating Custom Bucket Types

You can extend the bucket system with custom types beyond the standard `general`, `file`, and `category` types.

### Example: Creating a "Project" Bucket Type

```python
# Custom bucket type for project-based organization
def create_project_bucket_id(project_name: str, user_id: str) -> str:
    """Create a project bucket ID."""
    # Sanitize project name for use in bucket ID
    safe_name = project_name.lower().replace(" ", "_")
    return f"project:{safe_name}:{user_id}"


async def add_memory_to_project(
    svc,
    user_id: str,
    project_name: str,
    memory_content: str,
    metadata: dict = None,
):
    """Add a memory to a project bucket."""
    project_bucket_id = create_project_bucket_id(project_name, user_id)
    
    final_metadata = metadata or {}
    final_metadata["project_name"] = project_name
    
    return svc.inject(
        memory=memory_content,
        user_id=user_id,
        bucket_id=project_bucket_id,
        bucket_type="project",  # Custom type!
        metadata=final_metadata,
    )


async def add_file_to_project(
    svc,
    user_id: str,
    project_name: str,
    file_data: dict,
):
    """Add a file to a project bucket with proper association."""
    project_bucket_id = create_project_bucket_id(project_name, user_id)
    filename = file_data["filename"]
    
    # File gets its own bucket, associated with the project
    file_bucket_id = f"file:{filename}:{user_id}"
    
    return await process_and_store_file_memory(
        svc=svc,
        user_id=user_id,
        file_data=file_data,
        category=project_name,
        associated_bucket_id=project_bucket_id,  # Links to project!
    )


async def search_project(
    svc,
    user_id: str,
    project_name: str,
    query: str,
):
    """Search within a project bucket (includes both conversation and file memories)."""
    project_bucket_id = create_project_bucket_id(project_name, user_id)
    
    return svc.search(
        query=query,
        user_id=user_id,
        filters={"metadata": {"associated_bucket_id": project_bucket_id}},
    )
```

### Example: Creating a "Team" Bucket (Shared Across Users)

```python
# Team buckets - shared across multiple users
def create_team_bucket_id(team_id: str) -> str:
    """Create a team bucket ID (no user_id - shared!)."""
    return f"team:{team_id}"


async def add_memory_to_team(
    svc,
    user_id: str,  # Who added it
    team_id: str,
    memory_content: str,
):
    """Add a memory to a team bucket."""
    team_bucket_id = create_team_bucket_id(team_id)
    
    return svc.inject(
        memory=memory_content,
        user_id=user_id,  # Track who added it
        bucket_id=team_bucket_id,
        bucket_type="team",
        metadata={
            "team_id": team_id,
            "added_by": user_id,
            "shared": True,
        },
    )


async def search_team_memories(
    svc,
    team_id: str,
    query: str,
):
    """Search team memories (across all users in team)."""
    team_bucket_id = create_team_bucket_id(team_id)
    
    # Note: Don't filter by user_id for team searches
    return svc.search(
        query=query,
        user_id=None,  # No user filter
        filters={"metadata": {"bucket_id": team_bucket_id}},
    )
```

---

## Extending the System

### Adding Custom Metadata to File Memories

Extend the file processing to include custom metadata:

```python
async def process_file_with_custom_metadata(
    svc,
    user_id: str,
    file_data: dict,
    category: str,
    custom_metadata: dict = None,
):
    """Process a file with additional custom metadata."""
    filename = file_data["filename"]
    raw_text = file_data["raw_text"]
    
    file_bucket_id = f"file:{filename}:{user_id}"
    cat_bucket_id = f"bucket:{category}:{user_id}"
    
    # Merge custom metadata with standard fields
    base_metadata = {
        "filename": filename,
        "associated_bucket_id": cat_bucket_id,
        "category": category,
        "source": "document",
    }
    
    if custom_metadata:
        base_metadata.update(custom_metadata)
    
    # Extract and store facts
    chunks = semantic_chunking(raw_text)
    total_facts = 0
    
    for chunk in chunks:
        facts = await extract_facts_from_chunk(chunk)
        for fact in facts:
            svc.inject(
                memory=fact,
                user_id=user_id,
                bucket_id=file_bucket_id,
                bucket_type="file",
                metadata=base_metadata,
            )
            total_facts += 1
    
    return total_facts
```

### Creating a Bucket Manager Class

For complex applications, encapsulate bucket logic in a manager:

```python
class BucketManager:
    """Manages bucket creation, association, and querying."""
    
    def __init__(self, memory_service):
        self.svc = memory_service
    
    def category_bucket_id(self, category: str, user_id: str) -> str:
        """Generate a category bucket ID."""
        return f"bucket:{category}:{user_id}"
    
    def file_bucket_id(self, filename: str, user_id: str) -> str:
        """Generate a file bucket ID."""
        return f"file:{filename}:{user_id}"
    
    def project_bucket_id(self, project: str, user_id: str) -> str:
        """Generate a project bucket ID."""
        safe_name = project.lower().replace(" ", "_")
        return f"project:{safe_name}:{user_id}"
    
    async def add_to_category(
        self,
        user_id: str,
        category: str,
        memory: str,
        **kwargs,
    ):
        """Add a memory to a category bucket."""
        bucket_id = self.category_bucket_id(category, user_id)
        return self.svc.inject(
            memory=memory,
            user_id=user_id,
            bucket_id=bucket_id,
            bucket_type="category",
            metadata={
                "category": category,
                "associated_bucket_id": bucket_id,
                **kwargs.get("metadata", {}),
            },
            **{k: v for k, v in kwargs.items() if k != "metadata"},
        )
    
    async def add_file_to_category(
        self,
        user_id: str,
        category: str,
        file_data: dict,
    ):
        """Add a file to a category bucket."""
        filename = file_data["filename"]
        file_bid = self.file_bucket_id(filename, user_id)
        cat_bid = self.category_bucket_id(category, user_id)
        
        # Process file and associate with category
        return await self._process_file(
            user_id=user_id,
            file_data=file_data,
            file_bucket_id=file_bid,
            associated_bucket_id=cat_bid,
            category=category,
        )
    
    async def search_category(
        self,
        user_id: str,
        category: str,
        query: str,
        limit: int = 10,
    ):
        """Search within a category (includes files)."""
        bucket_id = self.category_bucket_id(category, user_id)
        return self.svc.search(
            query=query,
            user_id=user_id,
            limit=limit,
            filters={"metadata": {"associated_bucket_id": bucket_id}},
        )
    
    async def get_category_files(self, user_id: str, category: str):
        """Get all files in a category."""
        bucket_id = self.category_bucket_id(category, user_id)
        all_mems = self.svc.get_all(user_id=user_id, limit=2000)
        
        files = {}
        for m in all_mems:
            meta = m.get("metadata", {})
            if meta.get("associated_bucket_id") == bucket_id:
                bid = meta.get("bucket_id", "")
                if bid.startswith("file:") and bid not in files:
                    files[bid] = {
                        "bucket_id": bid,
                        "filename": meta.get("filename"),
                        "category": category,
                    }
        
        return list(files.values())
    
    async def _process_file(
        self,
        user_id: str,
        file_data: dict,
        file_bucket_id: str,
        associated_bucket_id: str,
        category: str,
    ):
        """Internal file processing method."""
        # Implementation would call semantic_chunking, fact extraction, etc.
        # Similar to process_and_store_file_memory() in sso-app-3
        pass
```

---

## Best Practices

### 1. Always Include `associated_bucket_id` for Files

Without `associated_bucket_id`, file memories won't appear in category searches:

```python
# GOOD: File memories appear in category searches
metadata = {
    "filename": filename,
    "associated_bucket_id": category_bucket_id,  # Required for unified search!
}

# BAD: File memories are orphaned
metadata = {
    "filename": filename,
    # Missing associated_bucket_id - won't appear in category searches
}
```

### 2. Use Consistent Bucket ID Patterns

Pick one pattern per bucket type and stick with it:

```python
# GOOD: Consistent pattern
category_bucket = f"bucket:{category}:{user_id}"
file_bucket = f"file:{filename}:{user_id}"

# BAD: Inconsistent patterns
bucket1 = f"cat-{category}-{user_id}"
bucket2 = f"{user_id}:category:{category}"
bucket3 = f"category_{category}_{user_id}"
```

### 3. Store Bucket Type for Filtering

The `bucket_type` field makes it easy to filter by memory type:

```python
# Store with bucket_type
svc.inject(
    memory=fact,
    user_id=user_id,
    bucket_id=file_bucket_id,
    bucket_type="file",  # Makes filtering easy!
)

# Later, filter by type
file_memories = svc.search(
    query=query,
    user_id=user_id,
    filters={"metadata": {"bucket_type": "file"}},
)
```

### 4. Include User ID in Bucket IDs

For user isolation, always include user_id in bucket patterns:

```python
# GOOD: User-isolated buckets
bucket_id = f"bucket:work:{user_id}"

# BAD: Shared bucket (unless intentional for team features)
bucket_id = "bucket:work"  # All users share this bucket!
```

### 5. Handle Category from Bucket ID

When you only have a bucket_id, extract the category:

```python
def extract_category_from_bucket(bucket_id: str) -> str:
    """Extract category name from bucket ID."""
    # bucket:work:user123 -> work
    # category:personal:user123 -> personal
    parts = bucket_id.split(":")
    if len(parts) >= 2:
        return parts[1]
    return "general"
```

---

## Summary

MDB-Engine's bucket system provides:

1. **Optional Runtime Parameters**: `bucket_id`, `bucket_type`, `associated_bucket_id`
2. **No Manifest Configuration Required**: Buckets are created at runtime
3. **Unified Search**: `associated_bucket_id` links files to categories
4. **Full Isolation**: Memories in one bucket don't leak to others
5. **Flexible Patterns**: Create custom bucket types as needed

The key to file memory is the **dual-bucket pattern**:
- Files get their own unique `bucket_id`: `file:{filename}:{user_id}`
- Files link to categories via `associated_bucket_id`: `bucket:{category}:{user_id}`
- Searching by `associated_bucket_id` finds both conversation AND file memories

For a complete implementation example, see the [sso-app-3 example](../../examples/advanced/sso-multi-app/apps/sso-app-3/).
