# OSO Cloud Hello World Example

A simple, beginner-friendly example demonstrating OSO Cloud authorization integration with automatic setup from `manifest.json`.

## What This Example Demonstrates

- **OSO Cloud Integration**: Using OSO Cloud (via Dev Server) for authorization
- **Manifest Auto-Setup**: OSO provider automatically initializes from `manifest.json` configuration
- **Permission-Based Access Control**: Simple read/write permissions on documents
- **engine.create_app() Pattern**: Modern mdb-engine lifecycle management

## Architecture

This example uses the `engine.create_app()` pattern for automatic lifecycle management:

```python
engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)
app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="OSO Cloud Hello World",
)
```

This pattern automatically handles:
- Engine initialization and shutdown
- Manifest loading and validation
- Auth setup from manifest (OSO provider, CORS, etc.)

```
┌─────────────────┐
│  web.py         │  FastAPI app with OSO authorization
│  (Port 8000)    │
└────────┬────────┘
         │
         │ Uses OsoAdapter
         │
┌────────▼────────┐
│  OSO Dev Server │  Local OSO Cloud API
│  (Port 8080)    │  Loads main.polar policy
└─────────────────┘
         │
         │ Stores facts
         │
┌────────▼────────┐
│  MongoDB        │  App data + auth
│  (Port 27017)   │
└─────────────────┘
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- That's it! Everything runs in containers.

### Run the Example

```bash
cd examples/basic/oso_hello_world
docker-compose up
```

This will:
1. Start OSO Dev Server (loads `main.polar` policy)
2. Start MongoDB
3. Build and start the FastAPI application
4. Auto-initialize OSO Cloud provider from `manifest.json`

The app will be available at: http://localhost:8000

## How It Works

### 1. OSO Dev Server

The OSO Dev Server is a local Docker container that provides the OSO Cloud API. It:
- Loads your `main.polar` policy file
- Stores authorization facts (roles, permissions)
- Watches for policy file changes

### 2. Manifest Auto-Setup

The `manifest.json` file configures OSO Cloud:

```json
{
  "auth": {
    "policy": {
      "provider": "oso",
      "authorization": {
        "initial_roles": [
          {"user": "alice@example.com", "role": "editor"},
          {"user": "bob@example.com", "role": "viewer"}
        ]
      }
    }
  }
}
```

When `engine.create_app()` is called, it automatically:
1. Reads the manifest configuration
2. Creates OSO Cloud client (reads `OSO_URL` and `OSO_AUTH` from env)
3. Initializes `OsoAdapter` with the client
4. Sets up initial roles and policies
5. Makes the provider available via `get_authz_provider` dependency

### 3. Authorization Checks

Routes use the OSO provider to check permissions:

```python
@app.get("/api/documents")
async def list_documents(
    request: Request,
    authz: AuthorizationProvider = Depends(get_authz_provider)
):
    user = await get_current_app_user(request)
    if not await authz.check(user.get("email"), "documents", "read"):
        raise HTTPException(403, "Permission denied")
    # ...
```

## API Endpoints

### Health & Status

- `GET /health` - Health check endpoint (for container orchestration)
- `GET /api/oso-status` - Check OSO Cloud connection status

### Authentication

- `POST /login` - Login with email/password
- `GET /logout` - Logout current user

### User Info

- `GET /api/me` - Get current user information and permissions

**Response:**
```json
{
  "user_id": "...",
  "email": "alice@example.com",
  "permissions": ["read", "write"],
  "role": "editor"
}
```

### Documents

- `GET /api/documents` - List all documents (requires `read` permission)
- `POST /api/documents` - Create a new document (requires `write` permission)

## Demo Users

The manifest configures two demo users:

- **alice@example.com** - Has `editor` role (can read and write)
- **bob@example.com** - Has `viewer` role (can only read)

Password for both: `password123`

### Testing with curl

1. **Login:**
```bash
curl -X POST http://localhost:8000/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "email=alice@example.com&password=password123" \
  -c cookies.txt
```

2. **Get user info:**
```bash
curl http://localhost:8000/api/me -b cookies.txt
```

3. **List documents (requires read permission):**
```bash
curl http://localhost:8000/api/documents -b cookies.txt
```

4. **Create document (requires write permission):**
```bash
curl -X POST http://localhost:8000/api/documents \
  -H "Content-Type: application/json" \
  -d '{"title": "Test", "content": "Hello World"}' \
  -b cookies.txt
```

## OSO Policy File

The `main.polar` file defines authorization rules:

```polar
actor User {}

resource Document {
    permissions = ["read", "write"];
    roles = ["viewer", "editor"];

    "read" if "viewer";
    "read" if "editor";
    "write" if "editor";
}
```

This means:
- Users with `viewer` role can `read` documents
- Users with `editor` role can `read` and `write` documents

## Environment Variables

Set in `docker-compose.yml`:

- `OSO_URL` - OSO Cloud URL (default: `http://oso-dev:8080` for Dev Server)
- `OSO_AUTH` - OSO Cloud API key (any dummy token works for Dev Server)
- `MONGO_URI` - MongoDB connection string
- `MONGO_DB_NAME` - Database name
- `APP_SECRET_KEY` - Application secret key

## Adding Users and Roles

### Via Manifest (Initial Setup)

Add to `manifest.json`:

```json
{
  "auth": {
    "policy": {
      "authorization": {
        "initial_roles": [
          {"user": "newuser@example.com", "role": "editor"}
        ]
      }
    }
  }
}
```

### Programmatically

```python
from mdb_engine.auth import get_authz_provider

authz = await get_authz_provider(request)

# Add role for user
await authz.add_role_for_user("newuser@example.com", "editor")

# Add permission for role
await authz.add_policy("editor", "documents", "write")
```

## Switching to Production OSO Cloud

To use production OSO Cloud instead of Dev Server:

1. **Get your API key** from https://cloud.osohq.com
2. **Update environment variables:**
   ```yaml
   environment:
     - OSO_URL=https://cloud.osohq.com  # or leave unset for default
     - OSO_AUTH=your_production_api_key_here
   ```
3. **Remove the `oso-dev` service** from `docker-compose.yml`
4. **Restart the app**

The same code works for both Dev Server and production!

## Project Structure

```
oso_hello_world/
├── docker-compose.yml    # Docker Compose configuration
├── Dockerfile            # Multi-stage Dockerfile
├── main.polar            # OSO authorization policy
├── manifest.json         # MDB_ENGINE manifest
├── README.md             # This file
├── requirements.txt      # Python dependencies
├── templates/
│   └── index.html        # Main HTML template
└── web.py                # FastAPI application
```

## Troubleshooting

### OSO Dev Server not starting

Check logs:
```bash
docker-compose logs oso-dev
```

Ensure `main.polar` exists and is valid.

### Authorization checks failing

1. Check that OSO provider was initialized:
   ```bash
   docker-compose logs app | grep "OSO"
   ```

2. Verify facts were added:
   - Check OSO Dev Server logs
   - Or query via OSO Cloud API

3. Check user email matches what's in OSO:
   ```python
   # In your code
   user_email = user.get("email")
   # This must match the email in initial_roles
   ```

### Permission denied errors

- Ensure user has the required role
- Check that policies are set up correctly
- Verify the policy file syntax is correct

## Next Steps

- Modify `main.polar` to add more complex authorization rules
- Add more resources and permissions
- Integrate with production OSO Cloud
- Explore OSO Cloud dashboard features

## Learn More

- [MDB_ENGINE Documentation](../../../README.md)
- [OSO Cloud Documentation](https://www.osohq.com/docs)
- [OSO Cloud Python SDK](https://pypi.org/project/oso-cloud/)
- [Polar Policy Language](https://docs.osohq.com/polar-syntax)
