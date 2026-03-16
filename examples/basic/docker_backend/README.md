# Docker Backend

A complete backend from two config files. No code. No dependencies to install.
Just `docker compose up`.

```
docker_backend/
  manifest.json        <- your entire backend definition
  docker-compose.yml   <- runs it
  dashboard.html       <- optional visual demo (open in browser)
```

That's it. You get:

- REST API with CRUD for 3 collections
- JSON Schema validation
- Filtering, sorting, pagination, field selection
- Soft delete with trash and restore
- Bulk insert
- Read-only collections
- Realtime WebSocket subscriptions via Change Streams
- Automatic timestamps
- OpenAPI docs at `/docs`

## Run

```bash
docker compose up
```

Wait for `Uvicorn running on http://0.0.0.0:8000` then:

- **Open the dashboard** -- open `dashboard.html` in your browser to see
  all three collections, CRUD, soft delete, and live realtime events
- **API docs** -- http://localhost:8000/docs (auto-generated Swagger UI)
- **Health** -- http://localhost:8000/health

## Test

### Create a task (schema-validated)

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Ship v1", "status": "pending", "priority": 1}' | python -m json.tool
```

### Reject bad data

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"status": "pending"}'
# -> 422: "title" is required
```

### List with filtering, sorting, and field selection

```bash
curl -s "http://localhost:8000/api/tasks?status=pending&sort=-created_at&fields=title,status" | python -m json.tool
```

### Count

```bash
curl -s "http://localhost:8000/api/tasks/_count?status=done" | python -m json.tool
```

### Soft delete lifecycle

```bash
TASK_ID="<paste _id from create response>"

# Delete (soft -- sets deleted_at)
curl -s -X DELETE "http://localhost:8000/api/tasks/$TASK_ID" | python -m json.tool

# View trash
curl -s "http://localhost:8000/api/tasks/_trash" | python -m json.tool

# Restore
curl -s -X POST "http://localhost:8000/api/tasks/$TASK_ID/_restore" | python -m json.tool
```

### Bulk insert notes

```bash
curl -s -X POST http://localhost:8000/api/notes/_bulk \
  -H "Content-Type: application/json" \
  -d '[
    {"text": "First note", "tag": "idea"},
    {"text": "Second note", "tag": "todo"},
    {"text": "Third note", "tag": "idea"}
  ]' | python -m json.tool
```

### Read-only collection

```bash
# GET works
curl -s "http://localhost:8000/api/system_log" | python -m json.tool

# POST blocked -> 405
curl -s -X POST http://localhost:8000/api/system_log \
  -H "Content-Type: application/json" \
  -d '{"event": "nope"}'
```

### Realtime (WebSocket)

Open a WebSocket connection to `ws://localhost:8000/ws/realtime` and subscribe:

```json
{"type": "subscribe", "collection": "tasks"}
```

Then create a task in another terminal -- you'll receive:

```json
{
  "type": "change",
  "collection": "tasks",
  "operation": "insert",
  "document_id": "...",
  "document": {"title": "Ship v1", "status": "pending", ...},
  "timestamp": "2026-03-15T12:00:00Z"
}
```

## How it works

The `manifest.json` defines three collections:

| Collection   | Features                                              |
|--------------|-------------------------------------------------------|
| `tasks`      | Full CRUD, schema validation, soft delete, realtime   |
| `notes`      | Full CRUD, bulk insert, schemaless                    |
| `system_log` | Read-only (GET only)                                  |

The `docker-compose.yml` starts two containers:

1. **api** -- installs `mdb-engine`, reads the manifest, serves the API
2. **mongo** -- `mongodb-atlas-local` (full Atlas feature set: replica set, Change Streams, Vector Search)

No Python. No JavaScript. No framework code. The manifest IS the backend.

## Clean up

```bash
docker compose down -v
```

## Next steps

- See [zero_code_api](../zero_code_api/) for the non-Docker version with detailed curl examples
- See [realtime_board](../realtime_board/) for a visual live-updating dashboard
- Add `"auth": {"required": true}` to protect collections
- Add `"memory_config": true` for AI memory capabilities
