# Multi-App Example: Cross-App Data Access

This example demonstrates secure cross-app data access using MDB_ENGINE's unified `MongoDBEngine` pattern with `create_app()` for automatic lifecycle management.

## Overview

The example consists of two applications:

1. **ClickTracker** (`apps/click_tracker/`) - Tracks user clicks and events
2. **Dashboard** (`apps/dashboard/`) - Admin dashboard that reads ClickTracker data for analytics

Both apps use the **unified MongoDBEngine pattern**:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI"),
    db_name=os.getenv("MONGODB_DB"),
)

# Automatic lifecycle management
app = engine.create_app(
    slug="click_tracker",
    manifest=Path(__file__).parent / "manifest.json",
)
```

### When to Use Multi-App Pattern

| Use Multi-App When... | Use Single-App When... |
|----------------------|----------------------|
| Building a SaaS platform | Building a standalone app |
| Multiple services share data | All data in one scope |
| Need cross-app analytics | No cross-service data needs |
| Microservices architecture | Monolithic application |
| Different teams own different apps | Single team owns everything |

### Key Features Demonstrated

- ✅ **Unified MongoDBEngine pattern** with `create_app()`
- ✅ **App-level authentication** with envelope encryption
- ✅ **Automatic secret management** - secrets auto-retrieved from encrypted storage
- ✅ **Cross-app data access** via manifest `read_scopes`
- ✅ **Secure token verification** on every database access
- ✅ **Data isolation** - automatic `app_id` filtering
- ✅ **Multi-site auto-detection** - engine detects from manifest
- ✅ **Docker Compose orchestration** for easy setup

## Architecture

```
┌─────────────────────────────────────────┐
│         Docker Compose                   │
│                                          │
│  ┌──────────────┐  ┌──────────────┐    │
│  │ ClickTracker │  │  Dashboard   │    │
│  │  Port 8000   │  │  Port 8001   │    │
│  └──────┬───────┘  └──────┬───────┘    │
│         │                 │             │
│         └────────┬────────┘             │
│                  │                      │
│         ┌────────▼────────┐            │
│         │    MongoDB       │            │
│         │  Port 27017     │            │
│         └─────────────────┘            │
└─────────────────────────────────────────┘
```

### Cross-App Access Flow

1. **ClickTracker** writes clicks to `click_tracker_clicks` collection
2. **Dashboard** reads from `click_tracker_clicks` (cross-app access)
3. Engine validates:
   - Dashboard's `app_token` matches stored secret
   - `click_tracker` is in Dashboard's `read_scopes`
   - Access is logged for audit

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.8+ (for setup script - only needed once)

### TL;DR - Just Run It

```bash
cd examples/multi_app
./scripts/setup.sh    # One-time setup (generates master key)
docker-compose up --build
# On first run: apps will generate secrets and exit
# Add secrets to .env, then: docker-compose restart click-tracker dashboard
```

### Step 1: Setup (One-Time)

Run the setup script to generate master key and create `.env` file:

```bash
cd examples/multi_app
./scripts/setup.sh
```

This will:
- Generate a master key
- Create `.env` file with the master key
- Set up all required environment variables

**Important**: You must run this script before `docker-compose up` the first time.

### Step 2: Start Services

Start MongoDB and apps (without OSO for now):

```bash
docker-compose up --build
```

Or with OSO Dev Server for authorization:

```bash
docker-compose --profile oso up --build
```

### Step 3: Retrieve App Secrets

After apps start, they will register and generate secrets. Check the logs:

```bash
# Check ClickTracker logs
docker-compose logs click-tracker | grep "Generated secret"

# Check Dashboard logs
docker-compose logs dashboard | grep "Generated secret"
```

You'll see output like:

```
⚠️  CLICK_TRACKER_SECRET not found in environment!
================================================================================
Generated secret: xK9mP2qR7sT4uV6wY8zA1bC3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA

Add this to your .env file:
CLICK_TRACKER_SECRET=xK9mP2qR7sT4uV6wY8zA1bC3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5zA

Then restart the service:
docker-compose restart click-tracker
================================================================================
```

### Step 4: Add Secrets to .env and Restart

Edit `.env` and add the secrets, then restart:

```bash
# Add secrets to .env file
CLICK_TRACKER_SECRET=<secret-from-logs>
DASHBOARD_SECRET=<secret-from-logs>

# Restart services
docker-compose restart click-tracker dashboard
```

**Note**: After adding secrets, the apps will start successfully. Subsequent runs will use the secrets from `.env`.

### Step 5: Verify Setup

Check health endpoints:

```bash
# ClickTracker health
curl http://localhost:8000/health

# Dashboard health
curl http://localhost:8001/health
```

Both should return `{"status": "healthy", ...}`.

## Testing

### Available Endpoints

**ClickTracker (Port 8000):**
- `GET /` - Root endpoint (lists available endpoints)
- `POST /track` - Track a click event
- `GET /clicks` - Get click history (optional: `?user_id=xxx&limit=100`)
- `GET /health` - Health check
- `GET /docs` - API documentation (Swagger UI)

**Dashboard (Port 8001):**
- `GET /` - Root endpoint (lists available endpoints)
- `GET /analytics` - Get click analytics (optional: `?hours=24`)
- `GET /dashboard` - Dashboard UI with analytics
- `GET /health` - Health check
- `GET /docs` - API documentation (Swagger UI)

### 🎯 Try the HTML Demo Pages!

**ClickTracker Demo:** http://localhost:8000/
- Interactive form to track clicks
- View recent clicks in real-time
- See statistics and analytics

**Dashboard:** http://localhost:8001/
- View analytics from ClickTracker
- See top URLs and click statistics
- Demonstrates cross-app data access

### Track Clicks (API)

```bash
curl -X POST http://localhost:8000/track \
  -H "Content-Type: application/json" \
  -d '{
    "url": "/page",
    "element": "button",
    "session_id": "session123",
    "user_id": "user1"
  }'
```

### View Clicks

```bash
# Get all clicks
curl http://localhost:8000/clicks

# Get clicks for specific user
curl http://localhost:8000/clicks?user_id=user1

# Limit results
curl http://localhost:8000/clicks?limit=10
```

### View Analytics

```bash
# Get analytics for last 24 hours (default)
curl http://localhost:8001/analytics

# Get analytics for last 48 hours
curl http://localhost:8001/analytics?hours=48
```

### View Dashboard

```bash
curl http://localhost:8001/dashboard
```

### Health Checks

```bash
curl http://localhost:8000/health
curl http://localhost:8001/health
```

### API Documentation

Open in browser:
- ClickTracker: http://localhost:8000/docs
- Dashboard: http://localhost:8001/docs

## Secret Management

### How Secrets Are Generated

When you register an app, the engine:
1. Generates a random 256-bit secret
2. Encrypts it using envelope encryption
3. Stores it in `_mdb_engine_app_secrets` collection

### Where to Find Secrets

**First Run:**
- Secrets are logged to stdout when apps start
- Copy secrets from logs and add to `.env`

**Subsequent Runs:**
- Secrets are read from `.env` file
- Apps verify secrets match stored encrypted values

### Rotating Secrets

To rotate a secret:

```python
# Connect to MongoDB and engine
from mdb_engine import MongoDBEngine

engine = MongoDBEngine(...)
await engine.initialize()

# Rotate secret
new_secret = await engine._app_secrets_manager.rotate_app_secret("click_tracker")

# Update .env file
# CLICK_TRACKER_SECRET=<new-secret>
```

## Environment Variables

See `.env.example` for all available variables. Key variables:

- `MDB_ENGINE_MASTER_KEY` - Required for encryption
- `CLICK_TRACKER_SECRET` - App secret (generated at registration)
- `DASHBOARD_SECRET` - App secret (generated at registration)

## Troubleshooting

### Apps Exit Immediately

**Problem**: Apps exit with "Secret not found" error.

**Solution**: 
1. Check logs for generated secrets
2. Add secrets to `.env` file
3. Restart services

### Cross-App Access Denied

**Problem**: Dashboard can't read ClickTracker data.

**Solution**:
1. Verify Dashboard manifest includes `click_tracker` in `read_scopes`
2. Check both apps are registered (check MongoDB `apps_config` collection)
3. Verify secrets are correct in `.env`

### Master Key Not Found

**Problem**: `ValueError: Master key not found`

**Solution**:
1. Run `./scripts/setup.sh` to generate master key
2. Or manually generate and add to `.env`:
   ```bash
   python3 -c 'from mdb_engine.core.encryption import EnvelopeEncryptionService; print(EnvelopeEncryptionService.generate_master_key())'
   ```

### MongoDB Connection Issues

**Problem**: Apps can't connect to MongoDB.

**Solution**:
1. Check MongoDB is healthy: `docker-compose ps mongodb`
2. Verify connection string in `.env` matches docker-compose service name
3. Check MongoDB logs: `docker-compose logs mongodb`

## How It Works

### Unified MongoDBEngine Pattern

Both apps in this example use the unified `MongoDBEngine` pattern with `create_app()`:

```python
from mdb_engine import MongoDBEngine
from pathlib import Path
import os

# Initialize engine
engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    db_name=os.getenv("MONGODB_DB", "mdb_runtime"),
)

# Create FastAPI app with automatic lifecycle management
app = engine.create_app(
    slug="click_tracker",
    manifest=Path(__file__).parent / "manifest.json",
)

@app.get("/clicks")
async def get_clicks():
    # Token auto-retrieved by engine
    db = engine.get_scoped_db("click_tracker")
    return await db.clicks.find({}).to_list(100)
```

The `create_app()` method automatically:
- Initializes the engine on startup
- Loads and validates the manifest
- Registers the app (generates secrets if needed)
- Auto-retrieves app tokens from environment or database
- Detects multi-site mode from manifest `read_scopes`
- Shuts down cleanly on app exit

### App-Level Authentication

Each app has a unique secret token that is:
1. **Generated automatically** during app registration
2. **Encrypted** using envelope encryption and stored in MongoDB
3. **Auto-retrieved** from the database if not in environment variables
4. **Verified** on every database access to ensure app identity

### Envelope Encryption

Secrets are encrypted using **envelope encryption**:
- **Master Key**: Stored in `MDB_ENGINE_MASTER_KEY` environment variable (generated by setup script)
- **Data Encryption Key (DEK)**: Generated per app, encrypted with master key
- **Secret Storage**: App secrets are encrypted with DEK and stored in `_mdb_engine_app_secrets` collection
- **Security**: Even if database is compromised, secrets remain encrypted

### Cross-App Data Access

The Dashboard app can read ClickTracker data because:
1. **Manifest Declaration**: Dashboard's manifest declares `"read_scopes": ["click_tracker_dashboard", "click_tracker"]`
2. **Token Verification**: Dashboard must provide valid `app_token` to access data
3. **Scope Validation**: Engine validates that requested `read_scopes` match manifest authorization
4. **Automatic Filtering**: All queries are automatically scoped to authorized apps

### Security Flow

```
1. App starts → Engine initializes → Secrets manager loads master key
2. App registers → Secret generated → Encrypted and stored in DB
3. App requests DB access → Token verified → Scoped DB returned
4. Query executed → Automatically filtered by app_id → Results returned
```

### Data Isolation

- Each app's data is **automatically isolated** by `app_id` field
- Apps can only access data from apps in their `read_scopes`
- Write operations are tagged with the app's `write_scope`
- Cross-app access requires explicit manifest declaration

## Architecture Details

### Data Flow

```
User Request → FastAPI App
    ↓
OSO Authorization Check (if configured)
    ↓
App Token Verification
    ↓
Scope Validation (read_scopes)
    ↓
Scoped Database Access
    ↓
MongoDB Query (with app_id filtering)
```

### Security Layers

1. **Envelope Encryption**: Secrets encrypted at rest
2. **App Token Verification**: Every access verifies app identity
3. **Manifest Authorization**: Declarative cross-app access
4. **Audit Logging**: All access attempts logged

## Related Documentation

- [Simple App Example](../simple_app/README.md) - Simpler example with create_app() pattern
- [App Authentication Guide](../../docs/APP_AUTHENTICATION.md) - Detailed authentication guide
- [Security Guide](../../docs/SECURITY.md) - Overall security architecture
- [Cross-App Access](../../docs/SECURITY.md#cross-app-access-control) - Cross-app access documentation
- [Quick Start Guide](../../docs/QUICK_START.md) - Getting started with MDB Engine

## File Structure

```
multi_app/
├── docker-compose.yml          # Orchestrates all services
├── Dockerfile                  # Shared Dockerfile for both apps
├── README.md                   # This file
├── .env.example                # Environment variables template
├── scripts/
│   └── setup.sh               # Setup script for master key
└── apps/
    ├── click_tracker/
    │   ├── manifest.json      # ClickTracker app manifest
    │   └── web.py             # ClickTracker FastAPI app
    └── dashboard/
        ├── manifest.json       # Dashboard app manifest
        └── web.py             # Dashboard FastAPI app
```

## Security Best Practices

### Production Deployment

1. **Master Key Management**:
   - Store `MDB_ENGINE_MASTER_KEY` in secure secret management (AWS Secrets Manager, HashiCorp Vault, etc.)
   - Never commit master key to version control
   - Rotate master key periodically

2. **App Secrets**:
   - Secrets are auto-generated and encrypted at rest
   - Store app secrets in environment variables for better performance
   - Rotate app secrets if compromised

3. **Network Security**:
   - Use TLS for MongoDB connections
   - Restrict network access to MongoDB
   - Use MongoDB authentication/authorization

4. **Manifest Security**:
   - Review `read_scopes` carefully - only grant necessary access
   - Use principle of least privilege
   - Audit manifest changes

### How Secrets Work

1. **First Run**: App registers → Secret generated → Encrypted → Stored in DB
2. **Subsequent Runs**: Secret auto-retrieved from encrypted storage
3. **Every Request**: Token verified → Access granted/denied
4. **No Manual Steps**: Fully automatic - no copying secrets manually

## Next Steps

- Explore the code to understand cross-app access patterns
- Modify manifests to experiment with different `read_scopes`
- Add more apps to demonstrate complex multi-app scenarios
- Review security documentation in `docs/SECURITY.md`
- Deploy to production with proper secret management

