# Task Board — Actions Demo

A minimal task board that showcases all three **mdb-engine Action** trigger types with zero custom backend code — just a manifest and three small action files.

## What this demonstrates

| Action file | Trigger type | What it does |
|---|---|---|
| `actions/auto-number.py` | **Event** (`after_create` on `tasks`) | Assigns a sequential `#number` to every new task |
| `actions/archive-done.py` | **Schedule** (daily) | Moves all `done` tasks into the `archive` collection |
| `actions/stats.py` | **HTTP** (`GET /actions/v1/stats`) | Returns live counts by status + archived total |

## Layout

```
task_board/
├── manifest.json          # 2 collections (tasks, archive) + 3 actions
├── public/
│   └── index.html         # Tailwind CSS dark-theme UI
├── actions/
│   ├── auto-number.py     # Event action
│   ├── archive-done.py    # Scheduled action
│   └── stats.py           # HTTP action
├── docker-compose.yml
└── README.md
```

No `web.py`. The entire backend is manifest + actions.

## Quick start

```bash
docker compose up
```

Open [http://localhost:8000](http://localhost:8000) — the Tailwind UI is served automatically from `public/`.

### Without Docker

```bash
# Start MongoDB locally, then:
export MONGODB_URI="mongodb://localhost:27017/?directConnection=true"
mdb-engine serve manifest.json
```

## Try it

### Create a task

```bash
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Ship the feature"}'
```

The `auto-number` event action fires and assigns `number: 1` automatically.

### Check stats

```bash
curl http://localhost:8000/actions/v1/stats
```

```json
{"todo": 1, "in_progress": 0, "done": 0, "archived": 0, "total": 1}
```

### Cycle a task's status

```bash
# Move to in_progress
curl -X PATCH http://localhost:8000/api/tasks/<id> \
  -H "Content-Type: application/json" \
  -d '{"status": "in_progress"}'

# Move to done
curl -X PATCH http://localhost:8000/api/tasks/<id> \
  -H "Content-Type: application/json" \
  -d '{"status": "done"}'
```

### View archived tasks

Once the `archive-done` scheduled action runs (daily, or trigger manually), completed tasks appear in the archive:

```bash
curl http://localhost:8000/api/archive
```
