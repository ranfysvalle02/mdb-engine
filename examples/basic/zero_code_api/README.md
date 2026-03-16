# Zero-Code API

A complete REST API from a single JSON file. No Python code required.

The manifest defines four collections with different feature profiles:

| Collection   | Features                                                       |
|--------------|----------------------------------------------------------------|
| `tasks`      | Full CRUD, schema validation, soft delete, defaults, scopes, pipelines |
| `projects`   | Full CRUD, document-level policy, defaults with user context, default projection |
| `comments`   | Full CRUD, bulk insert, schemaless                             |
| `audit_log`  | Read-only (GET only, no writes)                                |

## Run with Docker (easiest)

```bash
docker compose up
```

Open http://localhost:8000/docs -- your API is live.

## Run locally

### Prerequisites

- Python 3.10+
- MongoDB running (localhost:27017, Atlas, or set `MDB_MONGO_URI`)

```bash
pip install mdb-engine uvicorn
mdb-engine serve manifest.json --reload
```

Open the interactive docs at **http://localhost:8000/docs**.

## Test

### Create a task (schema-validated, defaults applied)

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Ship v1", "assignee": "alice"}' | python -m json.tool
```

The `status` and `priority` fields are auto-populated from `defaults`:

```json
{"status": "pending", "priority": 3}
```

### Reject an invalid task (missing required `title`)

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"status": "pending"}'
# -> 422 Validation error
```

### Defaults do not overwrite caller-provided values

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Urgent fix", "status": "in_progress", "priority": 5}' | python -m json.tool
# status stays "in_progress", priority stays 5
```

### Scopes -- named MQL filters activated via query parameter

```bash
# Active tasks only (status != "done")
curl -s "http://localhost:8000/api/tasks?scope=active" | python -m json.tool

# Completed tasks only
curl -s "http://localhost:8000/api/tasks?scope=done" | python -m json.tool

# High priority tasks (priority >= 4)
curl -s "http://localhost:8000/api/tasks?scope=high_priority" | python -m json.tool

# Combine scopes (AND logic): active AND high priority
curl -s "http://localhost:8000/api/tasks?scope=active,high_priority" | python -m json.tool

# Combine scopes with filters
curl -s "http://localhost:8000/api/tasks?scope=active&assignee=alice" | python -m json.tool
```

### Pipelines -- named aggregation endpoints

```bash
# Task count grouped by status
curl -s "http://localhost:8000/api/tasks/_agg/by_status" | python -m json.tool

# Task count grouped by assignee
curl -s "http://localhost:8000/api/tasks/_agg/by_assignee" | python -m json.tool
```

### List with filtering, sorting, pagination, and field selection

```bash
# Filter by status
curl -s "http://localhost:8000/api/tasks?status=pending" | python -m json.tool

# Sort by newest first
curl -s "http://localhost:8000/api/tasks?sort=-created_at" | python -m json.tool

# Paginate (page 2, 5 per page)
curl -s "http://localhost:8000/api/tasks?limit=5&skip=5" | python -m json.tool

# Only return title and status fields
curl -s "http://localhost:8000/api/tasks?fields=title,status" | python -m json.tool

# Combine them
curl -s "http://localhost:8000/api/tasks?status=pending&sort=-created_at&limit=5&fields=title,status" | python -m json.tool
```

### Count

```bash
curl -s "http://localhost:8000/api/tasks/_count" | python -m json.tool
curl -s "http://localhost:8000/api/tasks/_count?status=done" | python -m json.tool
curl -s "http://localhost:8000/api/tasks/_count?scope=active" | python -m json.tool
```

### Bulk insert comments

```bash
curl -s -X POST http://localhost:8000/api/comments/_bulk \
  -H "Content-Type: application/json" \
  -d '[
    {"text": "Looks good!", "author": "bob"},
    {"text": "Ship it", "author": "alice"},
    {"text": "+1", "author": "carol"}
  ]' | python -m json.tool
```

### Soft delete lifecycle

```bash
# Delete a task (sets deleted_at instead of removing)
TASK_ID="<paste an _id from the list response>"
curl -s -X DELETE "http://localhost:8000/api/tasks/$TASK_ID" | python -m json.tool

# It disappears from normal listings
curl -s "http://localhost:8000/api/tasks" | python -m json.tool

# But it shows up in the trash
curl -s "http://localhost:8000/api/tasks/_trash" | python -m json.tool

# Restore it
curl -s -X POST "http://localhost:8000/api/tasks/$TASK_ID/_restore" | python -m json.tool

# It's back in the normal listing
curl -s "http://localhost:8000/api/tasks" | python -m json.tool
```

### Document-level policy (projects collection)

The `projects` collection uses `policy` to restrict access to documents owned
by the authenticated user. All queries are automatically filtered by
`owner_id == user._id`.

```bash
# Requires authentication -- anonymous requests return 401
curl -s "http://localhost:8000/api/projects"
# -> 401

# With auth, only your projects are returned
# (owner_id is auto-set from defaults on create)
```

### Default projection (projects collection)

The `internal_notes` field is hidden from list/get responses by default.
Clients can override by specifying `?fields=name,internal_notes` explicitly.

### Read-only collection

```bash
# GET works fine
curl -s "http://localhost:8000/api/audit_log" | python -m json.tool

# POST is blocked -> 405
curl -s -X POST http://localhost:8000/api/audit_log \
  -H "Content-Type: application/json" \
  -d '{"event": "nope"}'
```

## What you get automatically

- **Auto-CRUD** -- GET, POST, PUT, PATCH, DELETE from a manifest definition
- **JSON Schema validation** -- rejects bad documents before they hit the database
- **Document-level policies** -- MQL filters in the manifest restrict read/write/delete per user
- **Named scopes** -- `?scope=active,mine` activates predefined MQL filters
- **Aggregation pipelines** -- `GET /api/{collection}/_agg/{name}` from manifest config
- **Document defaults** -- auto-populate fields on create (supports `{{user.*}}` templates)
- **Default projection** -- hide internal fields from API responses by default
- **Filtering** -- `?field=value`, `?field=gt:18`, `?field=in:a,b,c`
- **Sorting** -- `?sort=-created_at,name`
- **Pagination** -- `?limit=10&skip=20`
- **Field selection** -- `?fields=title,status`
- **Timestamps** -- `created_at` and `updated_at` injected automatically
- **Soft delete** -- delete, trash, restore lifecycle
- **Bulk insert** -- batch up to 1000 documents in one request
- **Read-only mode** -- GET-only collections for logs, audit trails, etc.
- **Data isolation** -- all queries scoped by `app_id` automatically
- **OpenAPI docs** -- Swagger UI at `/docs` with every endpoint documented

## The design principle

> **MQL is the DSL.** Every `policy`, `scopes`, `pipelines`, and `defaults`
> value is a native MongoDB Query Language expression written as JSON.
> The manifest speaks the same language as the database -- no translation
> layer, no custom syntax, no impedance mismatch.

## Next steps

- Add `"realtime": true` to a collection for live WebSocket updates -- see [realtime_board](../realtime_board/)
- Add `"auth": {"required": true}` to protect collections
- See [memory_quickstart](../memory_quickstart/) for adding AI memory
