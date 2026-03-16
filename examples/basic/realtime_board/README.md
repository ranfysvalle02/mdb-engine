# Realtime Board

A live task board powered by auto-CRUD and MongoDB Change Streams. Add a task
from one window and watch it appear instantly in another.

No Python code. One manifest, one HTML file.

## Run with Docker (easiest)

```bash
docker compose up
```

Then open `dashboard.html` in your browser and create tasks from another terminal.

## Run locally

### Prerequisites

- Python 3.10+
- MongoDB running (localhost:27017, Atlas, or set `MDB_MONGO_URI`)

**Terminal 1 -- start the server:**

```bash
pip install mdb-engine uvicorn
mdb-engine serve manifest.json --reload
```

**Browser -- open the dashboard:**

Open `dashboard.html` in your browser (double-click or drag into a tab).

**Terminal 2 -- create a task and watch the dashboard update:**

```bash
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Ship v1", "done": false}' | python -m json.tool
```

The task appears on the dashboard instantly without refreshing.

## How it works

### 1. Manifest enables everything

```json
"tasks": {
  "auto_crud": true,
  "realtime": true
}
```

- `auto_crud: true` generates REST endpoints at `/api/tasks`.
- `realtime: true` opens a MongoDB Change Stream and registers a WebSocket
  endpoint at `/ws/realtime`.

### 2. The dashboard connects over WebSocket

```js
const ws = new WebSocket('ws://localhost:8000/ws/realtime');

ws.onopen = () => {
  ws.send(JSON.stringify({ type: 'subscribe', collection: 'tasks' }));
};

ws.onmessage = (evt) => {
  const msg = JSON.parse(evt.data);
  // msg.type === 'change'
  // msg.collection === 'tasks'
  // msg.operation === 'insert' | 'update' | 'replace' | 'delete'
  // msg.document === { title: '...', done: false, ... }
};
```

### 3. Change Stream watcher dispatches events

Under the hood, mdb-engine opens a single database-level Change Stream
(`db.watch()`) filtered to collections with `realtime: true`. When a document
is inserted, updated, or deleted through the auto-CRUD API (or any other
means), the watcher picks up the event and pushes it to all subscribed
WebSocket clients.

```
curl POST /api/tasks
        |
        v
   MongoDB insert
        |
        v
  Change Stream event
        |
        v
  RealtimeManager.dispatch()
        |
        v
  WebSocket push to subscribers
        |
        v
  dashboard.html renders update
```

## Subscription protocol

**Client -> Server:**

```json
{"type": "subscribe", "collection": "tasks"}
{"type": "unsubscribe", "collection": "tasks"}
```

**Server -> Client (confirmation):**

```json
{"type": "subscribed", "collection": "tasks"}
{"type": "unsubscribed", "collection": "tasks"}
```

**Server -> Client (change event):**

```json
{
  "type": "change",
  "collection": "tasks",
  "operation": "insert",
  "document_id": "507f1f77bcf86cd799439011",
  "document": { "title": "Ship v1", "done": false, "created_at": "..." },
  "timestamp": "2026-03-15T12:00:00Z"
}
```

Operations: `insert`, `update`, `replace`, `delete`. On `delete`, `document`
is `null`.

## What you get automatically

- **Auto-CRUD API** -- full REST endpoints from a manifest definition
- **Change Stream watcher** -- single DB-level cursor, efficient on Atlas
- **WebSocket multiplexer** -- routes events to the correct subscribers
- **Graceful degradation** -- if Change Streams are unavailable, the API still
  works; only realtime subscriptions are disabled
- **Data isolation** -- events are scoped by `app_id` automatically

## Next steps

- See [zero_code_api](../zero_code_api/) for auto-CRUD features (filtering, soft delete, bulk insert)
- Add `"auth": {"required": true}` to protect collections
- See [memory_quickstart](../memory_quickstart/) for adding AI memory
