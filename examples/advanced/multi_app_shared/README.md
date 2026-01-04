# Multi-App with Shared Authentication (SSO)

This example demonstrates **shared authentication mode** where users authenticate once
and can access multiple apps (Single Sign-On).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Shared User Pool                            │
│        _mdb_engine_shared_users collection                      │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ User: alice@example.com                                  │   │
│  │ app_roles:                                               │   │
│  │   click_tracker: ["viewer"]                              │   │
│  │   dashboard: ["editor", "admin"]                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │ Same JWT Token (SSO)          │
              │                               │
     ┌────────▼────────┐            ┌────────▼────────┐
     │  Click Tracker  │            │    Dashboard    │
     │   Port 8000     │            │   Port 8001     │
     │                 │            │                 │
     │ require_role:   │            │ require_role:   │
     │   "viewer"      │            │   "editor"      │
     │                 │            │                 │
     │ ✓ alice can     │            │ ✓ alice can     │
     │   access        │            │   access        │
     └─────────────────┘            └─────────────────┘
```

## Features Demonstrated

1. **Shared User Pool**: Both apps share the same `_mdb_engine_shared_users` collection
2. **Single Sign-On (SSO)**: Login on Click Tracker, automatically authenticated on Dashboard
3. **Per-App Roles**: Users have different roles per app (viewer, editor, admin)
4. **Role Requirements**: Dashboard requires "editor" role, Click Tracker only needs "viewer"
5. **Cross-App Data Access**: Dashboard reads click data from Click Tracker's collections

## Auth Mode Configuration (manifest.json)

### Click Tracker (viewer access)
```json
{
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "default_role": "viewer",
    "require_role": "viewer",
    "public_routes": ["/health", "/api", "/login", "/register"]
  }
}
```

### Dashboard (editor access required)
```json
{
  "auth": {
    "mode": "shared",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "editor",
    "public_routes": ["/health", "/api", "/login"]
  }
}
```

## Running the Example

```bash
# From this directory
docker-compose up --build

# Or from project root
docker-compose -f examples/multi_app_shared/docker-compose.yml up --build
```

## Usage Flow

### 1. Register on Click Tracker
Visit http://localhost:8000 and register with email/password.
You'll get "viewer" role for Click Tracker and "editor" role for Dashboard by default.

### 2. Use Click Tracker
With "viewer" role, you can:
- Track clicks
- View your click history
- See click stats

### 3. Access Dashboard (SSO)
Visit http://localhost:8001. 
You are automatically logged in via SSO and can view analytics because new users now get "editor" role.

### 4. Admin Features (Optional)
To access admin features (like granting roles to others), you need "admin" role.
The first user can be manually promoted:

```bash
docker-compose exec mongo mongosh mdb_runtime_shared

# Update a user to have admin role
db._mdb_engine_shared_users.updateOne(
  {email: "your-email@example.com"},
  {$set: {"app_roles.dashboard": ["admin"], "app_roles.click_tracker": ["admin"]}}
)
```

### 5. Access Dashboard as Admin
Now you can:
- View analytics from Click Tracker
- Grant roles to other users

## Comparing Auth Modes

| Feature | mode: "app" | mode: "shared" |
|---------|-------------|----------------|
| User Storage | Per-app collection | Shared collection |
| Login | Per-app | SSO across apps |
| Tokens | App-specific | Shared JWT |
| Roles | N/A | Per-app roles |
| Use Case | Isolated apps | Platform apps |

## When to Use Shared Auth

Use **"shared"** mode when:
- Building a platform with multiple related apps
- You want users to login once (SSO)
- You need per-app role management
- Apps should share user identity

Use **"app"** mode (default) when:
- Apps are independent
- Each app has its own users
- Simpler setup without SSO needs

## Files Structure

```
multi_app_shared/
├── docker-compose.yml      # Orchestrates both apps
├── Dockerfile              # Shared build for both apps
├── README.md               # This file
└── apps/
    ├── click_tracker/
    │   ├── manifest.json   # auth.mode="shared", require_role="viewer"
    │   ├── web.py          # FastAPI app with auth endpoints
    │   └── templates/
    │       └── index.html  # UI with login/register
    └── dashboard/
        ├── manifest.json   # auth.mode="shared", require_role="editor"
        ├── web.py          # FastAPI app with admin features
        └── templates/
            └── index.html  # Admin UI
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGODB_URI` | MongoDB connection string | mongodb://localhost:27017 |
| `MONGODB_DB` | Database name | mdb_runtime_shared |
| `MDB_ENGINE_JWT_SECRET` | JWT secret for tokens | Auto-generated |

**Important**: All apps must use the same `MDB_ENGINE_JWT_SECRET` for SSO to work!

