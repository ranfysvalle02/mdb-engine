# SSO Multi-App: SSO Authentication Example

A complete working example demonstrating Single Sign-On (SSO) architecture where one central auth hub manages user authentication and two SSO-enabled apps validate tokens automatically.

**NEW**: This example now supports **multi-app mounting** - deploy all apps under a single FastAPI instance perfect for Render.com single-service deployments!

## Architecture Overview

```mermaid
graph TB
    AuthHub[SSO Auth Hub<br/>Port 8000<br/>- Registration<br/>- Login/Logout<br/>- Role Management]
    
    SSOApp1[pwd-zero<br/>Port 8001<br/>- Data Viewing]
    SSOApp2[FLUX<br/>Port 8002<br/>- Data Editing]
    
    MongoDB[(MongoDB<br/>Shared Database)]
    UserPool[Shared User Pool<br/>_mdb_engine_shared_users]
    
    AuthHub -->|Manages| UserPool
    AuthHub -->|Issues JWT Tokens| SSOApp1
    AuthHub -->|Issues JWT Tokens| SSOApp2
    
    SSOApp1 -->|Validates Tokens| UserPool
    SSOApp2 -->|Validates Tokens| UserPool
    
    AuthHub --> MongoDB
    SSOApp1 --> MongoDB
    SSOApp2 --> MongoDB
```

## Features

- **Auth Hub**: Handles user registration, login, logout, and role management
- **SSO Apps**: Validate tokens automatically via SSO middleware
- **Shared Authentication**: All apps use the same JWT secret and user pool
- **Role-Based Access**: Different roles (viewer, editor, admin) control access
- **SSO**: Login once, access all apps automatically
- **Per-App Roles**: Users can have different roles in different apps

## Quick Start

### Prerequisites

- Docker and Docker Compose (for multi-container setup)
- Python 3.11+ (for local development)
- MongoDB (local or Atlas)

### Running with Multi-App Mounting (Recommended for Single Deployment) 🆕

For **single-service deployments** (Render.com, Railway, Heroku), use the new multi-app mounting:

```bash
cd examples/advanced/sso-multi-app/apps
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000 --reload
```

**Access all apps on single port:**
- Auth Hub: http://localhost:8000/auth-hub
- pwd-zero: http://localhost:8000/pwd-zero
- FLUX: http://localhost:8000/flux
- Health: http://localhost:8000/health

**Benefits:**
- Single FastAPI instance
- Single port (perfect for Render.com)
- Shared engine and connection pool
- SSO works seamlessly

### Running with Docker Compose

1. **Clone and navigate to the example:**

```bash
cd examples/advanced/sso-multi-app
```

2. **Copy environment variables:**

```bash
cp .env.example .env
```

3. **Start all services:**

```bash
docker-compose up --build
```

4. **Access the apps:**

- **Auth Hub**: http://localhost:8000
- **pwd-zero**: http://localhost:8001
- **FLUX**: http://localhost:8002

### Running with Multi-App Mounting (Single FastAPI Instance) 🆕

For **single-deployment scenarios** (e.g., Render.com, Railway), use `create_multi_app()` to mount all apps under a single FastAPI instance:

1. **Using programmatic configuration:**

```python
# See apps/multi_app_main.py
from mdb_engine import MongoDBEngine
from pathlib import Path

engine = MongoDBEngine(mongo_uri=..., db_name=...)
app = engine.create_multi_app(
    apps=[
        {"slug": "auth-hub", "manifest": Path("./apps/auth-hub/manifest.json"), "path_prefix": "/auth-hub"},
        {"slug": "pwd-zero", "manifest": Path("./apps/sso-app-1/manifest.json"), "path_prefix": "/pwd-zero"},
        {"slug": "flux", "manifest": Path("./apps/sso-app-2/manifest.json"), "path_prefix": "/flux"}
    ]
)
```

2. **Using manifest-based configuration:**

```python
# See multi_app_manifest.json
app = engine.create_multi_app(
    multi_app_manifest=Path("./multi_app_manifest.json")
)
```

3. **Run the multi-app:**

```bash
cd apps
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000
```

4. **Access all apps on single port:**

- **Auth Hub**: http://localhost:8000/auth-hub
- **pwd-zero**: http://localhost:8000/pwd-zero
- **FLUX**: http://localhost:8000/flux
- **Health Check**: http://localhost:8000/health

**Benefits of Multi-App Mounting:**
- ✅ Single FastAPI instance (perfect for Render.com single service)
- ✅ Single port (no need for multiple ports)
- ✅ Shared engine and connection pool (resource efficient)
- ✅ SSO works seamlessly across mounted apps
- ✅ Unified health check endpoint
- ✅ Easy deployment (single service, single URL)

**Perfect For:**
- Render.com deployments (single service)
- Railway deployments
- Heroku deployments
- Any platform requiring single-service deployment
- Development environments (simpler setup)

### Running with Bundled Dockerfile (Single Container)

For simplified deployment, you can run all apps in a single container:

1. **Start bundled services:**

```bash
docker-compose -f docker-compose.bundled.yml up --build
```

2. **Access the apps** (same URLs as above)

**Benefits of Bundled Approach:**
- Single container instead of 4 separate containers
- Reduced resource usage (~1GB vs ~2GB)
- Simplified deployment and monitoring
- Same functionality as multi-container setup

**When to Use:**
- Development and testing environments
- Small to medium deployments
- When resource efficiency is important
- When you don't need independent scaling of apps

**When to Use Multi-Container:**
- Production environments requiring independent scaling
- When apps need different resource limits
- When you need to update apps independently

## Usage Flow

### 1. Register a User

1. Visit http://localhost:8000/register
2. Enter email and password
3. Click "Register"
4. You'll be automatically logged in and redirected to the dashboard

### 2. Access SSO Apps

After logging in on the auth hub:

1. Visit any SSO app (e.g., http://localhost:8001)
2. You'll be automatically authenticated via SSO
3. No need to login again!

### 3. Manage Roles (Admin Only)

1. Login as admin on auth hub
2. Go to Dashboard
3. View all users and their roles
4. Grant/revoke access to SSO apps

## App Descriptions

### Auth Hub (Port 8000)

- **Registration**: Create new user accounts
- **Login/Logout**: Authenticate users and issue JWT tokens
- **Dashboard**: View users and manage roles
- **Role Management**: Grant/revoke access to SSO apps

### pwd-zero (Port 8001)

- **Functionality**: Data viewing/listing
- **Required Role**: `viewer` (default)
- **Features**: View data, read-only operations

### FLUX (Port 8002)

- **Functionality**: Data editing
- **Required Role**: `viewer` (to access), `editor` or `admin` (to edit)
- **Features**: Create, read, update data

## Configuration

### Environment Variables

All apps share these critical environment variables:

- `MONGODB_URI`: MongoDB connection string
- `MONGODB_DB`: Database name (must be same for all apps)
- `MDB_ENGINE_JWT_SECRET`: JWT secret (must be same for all apps for SSO to work)

See `.env.example` for all available options.

### Manifest Configuration

Each app has a `manifest.json` file that configures:

- **Auth mode**: `"mode": "shared"` enables SSO
- **Auth hub URL**: `"auth_hub_url": "http://localhost:8000"` - URL of the authentication hub for redirecting unauthenticated users. Can be overridden via `AUTH_HUB_URL` environment variable
- **Roles**: Available roles for the app
- **Required role**: Minimum role needed to access
- **Public routes**: Routes that don't require authentication

**Example manifest.json for SSO apps:**
```json
{
  "auth": {
    "mode": "shared",
    "auth_hub_url": "http://localhost:8000",
    "roles": ["viewer", "editor", "admin"],
    "require_role": "viewer",
    "public_routes": ["/health", "/auth/callback"]
  }
}
```

**Configuration Priority**:
1. `manifest.auth.auth_hub_url` (declarative, versioned)
2. `AUTH_HUB_URL` environment variable (runtime override)
3. Default: `http://localhost:8000` (fallback)

## Architecture Details

### SSO Authentication Flow

1. **User registers/logs in** on auth hub
2. **Auth hub authenticates** via `SharedUserPool`
3. **JWT token issued** and stored in cookie
4. **SSO apps validate token** automatically via `SharedAuthMiddleware`
5. **User can access** all authorized SSO apps

### Token Validation

- Tokens are validated on every request by `SharedAuthMiddleware`
- User info is available via `request.state.user`
- Roles are checked per-app from `user.app_roles[app_slug]`

### Role Management

- Roles are stored per-app in `app_roles` field
- Auth hub can update roles via API endpoints
- Roles are checked by each SSO app independently

## Security Considerations

1. **Shared JWT Secret**: All apps must use the same `MDB_ENGINE_JWT_SECRET`
2. **Secure Cookies**: Tokens stored in HttpOnly cookies
3. **Role Validation**: Each app validates roles independently
4. **Token Revocation**: Logout revokes tokens across all apps
5. **HTTPS**: Use HTTPS in production (set `secure=True` in cookies)

## Development

### Running with Multi-App Mounting (Recommended) 🆕

**Single FastAPI instance - perfect for Render.com:**

1. **Install dependencies:**

```bash
pip install -e ".[casbin]"
pip install uvicorn fastapi jinja2 python-multipart
```

2. **Set environment variables:**

```bash
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DB="oblivio_apps"
export MDB_ENGINE_JWT_SECRET="your-secret-key"
```

3. **Start MongoDB:**

```bash
docker run -d -p 27017:27017 --name mongodb mongo:7
```

4. **Run multi-app (single instance):**

```bash
cd apps
uvicorn multi_app_main:app --host 0.0.0.0 --port 8000 --reload
```

**Access all apps:**
- Auth Hub: http://localhost:8000/auth-hub
- pwd-zero: http://localhost:8000/pwd-zero
- FLUX: http://localhost:8000/flux
- Health: http://localhost:8000/health

### Running Locally (without Docker) - Separate Instances

For development/testing with separate instances:

1. **Install dependencies:**

```bash
pip install -e ".[casbin]"
pip install uvicorn fastapi jinja2 python-multipart
```

2. **Set environment variables:**

```bash
export MONGODB_URI="mongodb://localhost:27017"
export MONGODB_DB="oblivio_apps"
export MDB_ENGINE_JWT_SECRET="your-secret-key"
```

3. **Start MongoDB:**

```bash
docker run -d -p 27017:27017 --name mongodb mongo:7
```

4. **Run each app separately:**

```bash
# Terminal 1 - Auth Hub
cd apps/auth-hub
python web.py

# Terminal 2 - pwd-zero
cd apps/sso-app-1
python web.py

# Terminal 3 - FLUX
cd apps/sso-app-2
python web.py
```

## Testing the Setup

1. **Start services**: `docker-compose up`
2. **Register user**: Visit http://localhost:8000/register
3. **Login**: Visit http://localhost:8000/login
4. **Access SSO apps**:
   - http://localhost:8001 (should auto-authenticate)
   - http://localhost:8002 (requires admin role)
5. **Manage roles**: Visit http://localhost:8000/dashboard (admin only)

## Troubleshooting

### SSO Not Working

- **Check JWT secret**: All apps must use the same `MDB_ENGINE_JWT_SECRET`
- **Check database**: All apps must use the same `MONGODB_DB`
- **Check cookies**: Ensure cookies are set with correct domain (localhost for local dev)

### Can't Access SSO Apps

- **Check roles**: User must have appropriate role for the app
- **Check manifest**: Verify `require_role` in manifest.json
- **Check logs**: Check container logs for authentication errors

### Role Management Not Working

- **Check admin access**: Only admins can manage roles
- **Check API endpoints**: Verify `/api/users/{email}/roles/{app_slug}` endpoint
- **Check database**: Verify user exists in `_mdb_engine_shared_users` collection

## Comparison with Other Auth Modes

| Feature | `mode: "app"` | `mode: "shared"` (This Example) |
|---------|---------------|----------------------------------|
| User Storage | Per-app collection | Shared collection |
| Login | Per-app | SSO across apps |
| Tokens | App-specific | Shared JWT |
| Roles | N/A | Per-app roles |
| Use Case | Isolated apps | Platform apps |

## Render.com Deployment

For **single-service deployment on Render.com**, use the multi-app mounting approach:

1. **Set build command:**
   ```bash
   pip install -e ".[casbin]" && pip install uvicorn fastapi jinja2 python-multipart
   ```

2. **Set start command:**
   ```bash
   cd examples/advanced/sso-multi-app/apps && uvicorn multi_app_main:app --host 0.0.0.0 --port $PORT
   ```

3. **Set environment variables:**
   - `MONGODB_URI` - Your MongoDB connection string
   - `MONGODB_DB` - Database name
   - `MDB_ENGINE_JWT_SECRET` - JWT secret (same for all apps)

4. **Deploy!** All apps accessible under one URL with path prefixes.

## Related Examples

- [Multi-App Shared](../multi_app_shared/README.md) - Similar SSO example
- [Multi-App](../multi_app/README.md) - Cross-app data access
- [Simple App](../simple_app/README.md) - Basic app setup

## File Structure

```
sso-multi-app/
├── README.md                    # This file
├── docker-compose.yml           # Multi-container orchestration
├── docker-compose.bundled.yml   # Single-container orchestration
├── Dockerfile                   # Multi-container build
├── Dockerfile.bundled           # Single-container build
├── start-all-apps.py            # Bundled startup script
├── multi_app_manifest.json      # Multi-app manifest (NEW)
├── .env.example                 # Environment template
└── apps/
    ├── multi_app_main.py         # Multi-app main file (NEW)
    ├── auth-hub/                # Auth hub (central authentication)
    │   ├── manifest.json
    │   ├── web.py
    │   └── templates/
    ├── sso-app-1/               # pwd-zero
    │   ├── manifest.json
    │   ├── web.py
    │   └── templates/
    └── sso-app-2/               # FLUX
        ├── manifest.json
        ├── web.py
        └── templates/
```

## Deployment Options Comparison

| Approach | Use Case | Ports | Complexity | Best For |
|----------|----------|-------|------------|----------|
| **Multi-App Mounting** | Single service deployment | 1 | Low | Render.com, Railway, Heroku |
| **Bundled Container** | Single container, multiple processes | Multiple | Medium | Docker deployments |
| **Multi-Container** | Independent scaling | Multiple | High | Production, microservices |

## License

This example is part of MDB-Engine and follows the same license.
