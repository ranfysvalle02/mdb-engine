# Hello World

The absolute simplest mdb-engine application. No manifest file needed.

## Prerequisites

- Python 3.8+
- MongoDB running (localhost:27017 or set `MDB_MONGO_URI`)

## Run

```bash
pip install mdb-engine fastapi uvicorn
uvicorn web:app --reload
```

## Test

```bash
# Create an item
curl -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Widget", "price": 9.99}'

# List items
curl http://localhost:8000/items
```

## What you get automatically

- **Data isolation**: all queries scoped by `app_id`
- **Collection prefixing**: `db.items` becomes `hello_items`
- **Lifecycle management**: engine startup/shutdown handled for you

## Next steps

- Add a `manifest.json` for index management, auth, or AI features
- See [memory_quickstart](../memory_quickstart/) for adding AI memory
- See [chit_chat](../chit_chat/) for a full-featured AI chat app
