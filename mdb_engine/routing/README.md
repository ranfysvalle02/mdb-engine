# WebSocket Support in MDB_ENGINE

MDB_ENGINE provides built-in WebSocket support with app-level isolation and automatic route registration via `manifest.json`.

## Quick Start

### 1. Add WebSocket to manifest.json

```json
{
  "slug": "my_app",
  "websockets": {
    "realtime": {
      "path": "/ws"
    }
  }
}
```

That's it! The engine automatically:
- ✅ Registers the WebSocket route with FastAPI
- ✅ Creates an isolated connection manager for your app
- ✅ Handles authentication (uses app's `auth_policy` by default)
- ✅ Manages connection lifecycle
- ✅ Provides ping/pong keepalive

### 2. Broadcast messages (Server → Clients)

Send messages from your server code to connected clients:

```python
from mdb_engine.routing.websockets import broadcast_to_app

# Broadcast to all connected clients for your app
await broadcast_to_app("my_app", {
    "type": "update",
    "data": {"status": "completed"}
})

# Broadcast to specific user only
await broadcast_to_app("my_app", {
    "type": "notification",
    "data": {"message": "Hello"}
}, user_id="user123")
```

### 3. Listen to client messages (Clients → Server)

Handle messages sent from clients to your server:

```python
from mdb_engine.routing.websockets import register_message_handler, broadcast_to_app

async def handle_client_message(websocket, message):
    """Handle incoming messages from WebSocket clients."""
    message_type = message.get("type")

    if message_type == "subscribe":
        # Client wants to subscribe to a channel
        channel = message.get("channel")
        await broadcast_to_app("my_app", {
            "type": "subscribed",
            "channel": channel,
            "user_id": message.get("user_id")
        })

    elif message_type == "ping":
        # Respond to custom ping
        await broadcast_to_app("my_app", {
            "type": "pong",
            "timestamp": message.get("timestamp")
        }, user_id=message.get("user_id"))

# Register handler for the "realtime" endpoint
register_message_handler("my_app", "realtime", handle_client_message)
```

**Note:** Register handlers **before** calling `engine.register_app()` or `engine.register_websocket_routes()`.

## Configuration Options

### Minimal Configuration (Recommended)

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws"
    }
  }
}
```

**Defaults:**
- ✅ Authentication: Uses app's `auth_policy` (respects `auth_required` and `auth_policy.required`)
- ✅ Ping interval: 30 seconds
- ✅ Automatic route registration
- ✅ App-level isolation (secure by default)

### Advanced Configuration

```json
{
  "websockets": {
    "realtime": {
      "path": "/ws",
      "description": "Real-time updates",
      "auth": {
        "required": true,
        "allow_anonymous": false
      },
      "ping_interval": 30
    },
    "events": {
      "path": "/events",
      "auth": {
        "required": false
      },
      "description": "Public event stream"
    }
  }
}
```

## Security Features

### App-Level Isolation

- Each app has its own `WebSocketConnectionManager` instance
- Connections are automatically scoped to the app's `slug`
- Messages include `app_slug` in metadata to prevent cross-app leakage
- Broadcasts only reach clients connected to that specific app

### Authentication

- Integrates with mdb_engine's JWT authentication system
- Supports token via query parameter or cookie
- Respects app's `auth_policy` configuration
- Can be overridden per endpoint

### Connection Metadata

Each connection tracks:
- `app_slug`: Ensures isolation
- `user_id`: For user-specific filtering
- `user_email`: For logging and debugging
- `connected_at`: Connection timestamp

## Usage Examples

### Broadcasting (Server → Clients)

#### Basic Broadcast

```python
from mdb_engine.routing.websockets import broadcast_to_app

# After a database operation
await db.collection.insert_one(document)
await broadcast_to_app("my_app", {
    "type": "document_created",
    "data": {"id": str(document["_id"])}
})
```

#### User-Specific Broadcast

```python
# Send notification to specific user
await broadcast_to_app("my_app", {
    "type": "notification",
    "data": {"message": "Task completed"}
}, user_id=current_user_id)
```

#### Broadcast After CRUD Operations

```python
# After creating a document
result = await db.items.insert_one(item)
await broadcast_to_app("my_app", {
    "type": "item_created",
    "data": {"id": str(result.inserted_id), "item": item}
})

# After updating
await db.items.update_one({"_id": item_id}, {"$set": updates})
await broadcast_to_app("my_app", {
    "type": "item_updated",
    "data": {"id": str(item_id), "updates": updates}
})

# After deleting
await db.items.delete_one({"_id": item_id})
await broadcast_to_app("my_app", {
    "type": "item_deleted",
    "data": {"id": str(item_id)}
})
```

### Listening (Clients → Server)

#### Basic Message Handler

```python
from mdb_engine.routing.websockets import register_message_handler, broadcast_to_app

async def handle_realtime_messages(websocket, message):
    """Handle messages from WebSocket clients."""
    msg_type = message.get("type")

    if msg_type == "subscribe":
        # Client subscribes to updates
        channel = message.get("channel", "default")
        await broadcast_to_app("my_app", {
            "type": "subscribed",
            "channel": channel
        })

    elif msg_type == "unsubscribe":
        # Client unsubscribes
        await broadcast_to_app("my_app", {
            "type": "unsubscribed"
        })

    elif msg_type == "request_data":
        # Client requests specific data
        data_type = message.get("data_type")
        # Fetch and send data
        data = await fetch_data(data_type)
        await broadcast_to_app("my_app", {
            "type": "data_response",
            "data_type": data_type,
            "data": data
        })

# Register before routes are registered
register_message_handler("my_app", "realtime", handle_realtime_messages)
```

#### Advanced: User-Aware Handler

```python
from mdb_engine.routing.websockets import register_message_handler, get_websocket_manager

async def handle_user_actions(websocket, message):
    """Handle user actions with authentication context."""
    # Get connection metadata to identify user
    manager = await get_websocket_manager("my_app")
    connection = next(
        (conn for conn in manager.active_connections if conn.websocket is websocket),
        None
    )

    if connection and connection.user_id:
        user_id = connection.user_id
        msg_type = message.get("type")

        if msg_type == "update_preferences":
            # Update user preferences
            prefs = message.get("preferences")
            await db.users.update_one(
                {"_id": user_id},
                {"$set": {"preferences": prefs}}
            )
            # Notify user of success
            await broadcast_to_app("my_app", {
                "type": "preferences_updated",
                "user_id": user_id
            }, user_id=user_id)

register_message_handler("my_app", "realtime", handle_user_actions)
```

#### Error Handling in Handlers

```python
async def safe_message_handler(websocket, message):
    """Handler with error handling."""
    try:
        msg_type = message.get("type")
        if msg_type == "action":
            # Process action
            result = await process_action(message.get("action"))
            await broadcast_to_app("my_app", {
                "type": "action_result",
                "result": result
            })
    except (ValueError, TypeError, KeyError) as e:
        logger.error(f"Error handling message: {e}")
        # Send error to client
        manager = await get_websocket_manager("my_app")
        await manager.send_to_connection(websocket, {
            "type": "error",
            "message": str(e)
        })

register_message_handler("my_app", "realtime", safe_message_handler)
```

### Connection Management

#### Get Connection Manager

```python
from mdb_engine.routing.websockets import get_websocket_manager

manager = await get_websocket_manager("my_app")
connection_count = manager.get_connection_count()
user_connections = manager.get_connections_by_user("user123")
```

#### Check Connection Status

```python
manager = await get_websocket_manager("my_app")

# Total connections
total = manager.get_connection_count()

# Connections for specific user
user_conns = manager.get_connections_by_user("user123")
user_count = manager.get_connection_count_by_user("user123")
```

#### Send to Specific Connection

```python
manager = await get_websocket_manager("my_app")

# Find user's connection
user_connections = manager.get_connections_by_user("user123")
if user_connections:
    await manager.send_to_connection(
        user_connections[0].websocket,
        {"type": "personal_message", "data": "Hello!"}
    )
```

## Automatic Route Registration

When you call `engine.register_app(manifest)`, WebSocket routes are automatically registered:

```python
from mdb_engine import MongoDBEngine
from mdb_engine.routing.websockets import register_message_handler

# 1. Register message handlers FIRST (before route registration)
async def handle_messages(websocket, message):
    # Your handler logic
    pass

register_message_handler("my_app", "realtime", handle_messages)

# 2. Initialize engine and register app
engine = MongoDBEngine(mongo_uri="...", db_name="...")
await engine.initialize()

manifest = await engine.load_manifest("manifest.json")
await engine.register_app(manifest)  # WebSocket config loaded here

# 3. Register routes with FastAPI app (routes are created here)
engine.register_websocket_routes(app, manifest["slug"])
```

**Important:** Register message handlers **before** calling `register_websocket_routes()` so handlers are available when routes are created.

## Two-Way Communication

MDB_ENGINE WebSockets support **both directions**:

### Server → Clients (Broadcasting)
- Use `broadcast_to_app()` to send messages to clients
- Messages are automatically scoped to the app
- Can filter by user_id for targeted messages
- Perfect for: real-time updates, notifications, data changes

### Clients → Server (Listening)
- Register handlers with `register_message_handler()`
- Handlers process incoming client messages
- Can respond with broadcasts or direct messages
- Perfect for: subscriptions, user actions, data requests

### Complete Example

```python
from mdb_engine.routing.websockets import (
    register_message_handler,
    broadcast_to_app
)

# 1. Register handler to LISTEN to client messages
async def handle_client_requests(websocket, message):
    msg_type = message.get("type")

    if msg_type == "get_status":
        # Client requests status - respond with broadcast
        status = await get_current_status()
        await broadcast_to_app("my_app", {
            "type": "status_update",
            "data": status
        })

    elif msg_type == "subscribe_channel":
        # Client subscribes to channel
        channel = message.get("channel")
        await subscribe_user_to_channel(message.get("user_id"), channel)

register_message_handler("my_app", "realtime", handle_client_requests)

# 2. BROADCAST messages from your app code
async def on_document_created(document):
    await broadcast_to_app("my_app", {
        "type": "document_created",
        "data": {"id": str(document["_id"])}
    })
```

## Best Practices

1. **Keep manifest simple**: Only specify `path` in manifest.json - defaults are secure
2. **Use `broadcast_to_app()`**: Simplest way to send messages to clients
3. **Register handlers early**: Register message handlers before route registration
4. **Respect auth_policy**: Let the engine handle authentication automatically
5. **Scope messages**: Always include `app_slug` (automatically added by engine)
6. **Filter by user**: Use `user_id` parameter for user-specific messages
7. **Handle errors**: Wrap handler logic in try/except and send error responses
8. **Use message types**: Structure messages with `type` field for easy routing

## Architecture

- **Isolation**: Each app has isolated WebSocket manager (no cross-app access)
- **Security**: Messages automatically scoped to app, authentication integrated
- **Simplicity**: Just declare in manifest.json, register handlers in code
- **Flexibility**: Full two-way communication (broadcast + listen)
- **Automatic**: Routes registered automatically during app registration

## Message Flow

```
Client                    Server                    App Code
  |                         |                          |
  |--- WebSocket Connect -->|                          |
  |                         |--- Authenticate ------->|
  |<-- Connection Confirmed-|                          |
  |                         |                          |
  |--- Message: {"type":    |                          |
  |     "subscribe"} ------>|                          |
  |                         |--- Handler Called ------>|
  |                         |                          |
  |                         |<-- broadcast_to_app() ---|
  |<-- Broadcast Message ---|                          |
  |                         |                          |
```

## Security Considerations

1. **App Isolation**: Each app's WebSocket manager is completely isolated
2. **Authentication**: Uses app's `auth_policy` by default
3. **Message Scoping**: All messages include `app_slug` automatically
4. **User Context**: Connection metadata tracks user_id and user_email
5. **Error Handling**: Handler errors don't crash the connection
