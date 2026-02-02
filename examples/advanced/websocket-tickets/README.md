# WebSocket Tickets Multi-App Example

A complete working example demonstrating **ticket-based WebSocket authentication** in a **multi-app setup** using `create_multi_app()`.

## Overview

This example showcases:

- **Multi-app architecture** using `create_multi_app()` - three apps mounted under a single FastAPI instance
- **Ticket-based WebSocket authentication** - secure, short-lived (10 seconds), single-use tickets
- **Real-time functionality** - chat and notifications via WebSocket broadcasting
- **Minimal MDB-Engine usage** - only database operations, no AI/embeddings

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Parent App (Port 8000)                      │
│  ┌───────────────────────────────────────────────────┐   │
│  │  /auth/ticket  (Ticket endpoint)                  │   │
│  └───────────────────────────────────────────────────┘   │
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  Auth Hub    │  │  Chat App    │  │Notifications │   │
│  │  /auth-hub   │  │  /chat-app   │  │  /notifications│ │
│  │              │  │              │  │              │   │
│  │  • Register  │  │  • WebSocket  │  │  • WebSocket │   │
│  │  • Login     │  │  • Chat UI   │  │  • Notif UI  │   │
│  │  • JWT Cookie│  │  • Messages  │  │  • Broadcast│   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└───────────────────────────────────────────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │    MongoDB       │
              │  • Users         │
              │  • Messages     │
              │  • Notifications │
              └─────────────────┘
```

## Features

### 1. Multi-App Setup

- **Auth Hub** (`/auth-hub`) - User registration and login
- **Chat App** (`/chat-app`) - Real-time chat with WebSocket
- **Notifications App** (`/notifications-app`) - Real-time notifications with WebSocket

All apps are mounted using `create_multi_app()` and share:
- Same MongoDB database
- Same authentication (shared auth mode)
- Same ticket endpoint (`/auth/ticket`)

### 2. Ticket-Based Authentication

**Security Flow:**
1. User logs in at `/auth-hub/login` → JWT stored in httpOnly cookie
2. Client requests ticket → `POST /auth/ticket` (sends JWT cookie)
3. Server validates JWT → Generates one-time ticket (UUID, 10-second TTL)
4. Client connects WebSocket → `ws://host/chat-app/ws?ticket=<uuid>`
5. Server validates ticket → Consumes ticket (single-use, atomic operation)
6. WebSocket connection established

**Security Benefits:**
- ✅ Short-lived (10 seconds) - reduces interception window
- ✅ Single-use - tickets consumed immediately (prevents replay attacks)
- ✅ In-memory storage - no database lookups (faster)
- ✅ No dependencies - works without encryption service

### 3. Real-Time Functionality

**Chat App:**
- Send messages via REST API (`POST /chat-app/api/messages`)
- Messages broadcast to all connected clients via WebSocket
- Messages stored in MongoDB

**Notifications App:**
- Create notifications via REST API (`POST /notifications-app/api/notifications`)
- Notifications broadcast to all connected clients via WebSocket
- Notifications stored in MongoDB

## Quick Start

### Prerequisites

- Docker and Docker Compose (recommended)
- OR Python 3.11+ and MongoDB (for local development)

### With Docker Compose (Recommended)

```bash
cd examples/advanced/websocket-tickets
docker-compose up --build
```

Open http://localhost:8000

### Without Docker

```bash
# Start MongoDB
mongod --dbpath /tmp/mongodb

# Install dependencies
pip install -r requirements.txt
pip install -e ../..  # Install mdb-engine from project root

# Run the app
cd apps
uvicorn multi_app_main:app --reload
```

Open http://localhost:8000

## Usage

### 1. Register and Login

1. Navigate to http://localhost:8000 (redirects to `/auth-hub`)
2. Click "Register" to create a new account
3. Fill in name, email, and password
4. After registration, you're automatically logged in (JWT cookie set)

### 2. Use Chat App

1. Navigate to http://localhost:8000/chat-app
2. Click "Get Ticket & Connect" button
3. WebSocket connects automatically with ticket
4. Type messages and see them broadcast in real-time
5. Open multiple browser tabs to see real-time updates

### 3. Use Notifications App

1. Navigate to http://localhost:8000/notifications-app
2. Click "Get Ticket & Connect" button
3. WebSocket connects automatically with ticket
4. Create notifications and see them broadcast in real-time
5. Open multiple browser tabs to see real-time updates

## API Endpoints

### Auth Hub

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth-hub/` | GET | Redirects to login or chat-app |
| `/auth-hub/login` | GET | Login page |
| `/auth-hub/login` | POST | Login (sets JWT cookie) |
| `/auth-hub/register` | GET | Registration page |
| `/auth-hub/register` | POST | Register new user |
| `/auth-hub/logout` | POST | Logout (clears JWT cookie) |

### Chat App

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat-app/` | GET | Chat UI |
| `/chat-app/api/messages` | GET | Get recent messages |
| `/chat-app/api/messages` | POST | Send message (broadcasts via WebSocket) |
| `/chat-app/ws` | WebSocket | Chat WebSocket endpoint (requires ticket) |

### Notifications App

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/notifications-app/` | GET | Notifications UI |
| `/notifications-app/api/notifications` | GET | Get recent notifications |
| `/notifications-app/api/notifications` | POST | Create notification (broadcasts via WebSocket) |
| `/notifications-app/ws` | WebSocket | Notifications WebSocket endpoint (requires ticket) |

### Parent App

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/ticket` | POST | Exchange JWT for ticket (requires authentication) |
| `/health` | GET | Health check |
| `/info` | GET | App information |

## WebSocket Message Format

### Chat App Messages

**Client → Server:**
```json
{
  "type": "chat",
  "text": "Hello, world!"
}
```

**Server → Client:**
```json
{
  "type": "message",
  "text": "Hello, world!",
  "user_email": "user@example.com",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

### Notifications App Messages

**Client → Server:**
```json
{
  "type": "subscribe",
  "user_id": "user123"
}
```

**Server → Client:**
```json
{
  "type": "notification",
  "title": "New Message",
  "message": "You have a new message",
  "user_id": "user123",
  "created_by": "system@example.com",
  "timestamp": "2024-01-01T12:00:00Z"
}
```

## Ticket Authentication Flow

### Step-by-Step

1. **Login** → User logs in at `/auth-hub/login`
   - JWT token generated and stored in httpOnly cookie
   - Cookie sent automatically with subsequent requests

2. **Get Ticket** → Client requests ticket from `/auth/ticket`
   ```javascript
   const res = await fetch('/auth/ticket', {
     method: 'POST',
     credentials: 'include'  // Sends JWT cookie
   });
   const { ticket } = await res.json();
   ```

3. **Connect WebSocket** → Client connects with ticket
   ```javascript
   const ws = new WebSocket(`ws://localhost:8000/chat-app/ws?ticket=${ticket}`);
   ```

4. **Server Validates** → Server validates and consumes ticket
   - Ticket checked for existence and expiration
   - Ticket consumed immediately (single-use)
   - WebSocket connection established

### Why Tickets?

- **Security**: Short-lived (10 seconds) reduces interception window
- **Single-use**: Consumed immediately prevents replay attacks
- **Simplicity**: No database lookups, faster validation
- **No dependencies**: Works without encryption service

## File Structure

```
websocket-tickets/
├── apps/
│   ├── multi_app_main.py          # Main file using create_multi_app()
│   ├── auth-hub/
│   │   ├── web.py                  # Auth hub routes
│   │   ├── manifest.json           # Auth hub manifest
│   │   └── templates/
│   │       ├── login.html          # Login page
│   │       └── register.html       # Registration page
│   ├── chat-app/
│   │   ├── web.py                  # Chat app routes and WebSocket handler
│   │   ├── manifest.json           # Chat app manifest with WebSocket
│   │   └── templates/
│   │       └── index.html          # Chat UI with ticket demo
│   └── notifications-app/
│       ├── web.py                  # Notifications app routes and WebSocket handler
│       ├── manifest.json           # Notifications app manifest with WebSocket
│       └── templates/
│           └── index.html          # Notifications UI with ticket demo
├── requirements.txt                # Python dependencies
├── docker-compose.yml              # Docker setup
├── Dockerfile                      # Docker image
└── README.md                       # This file
```

## Key Demonstrations

1. **Multi-App Architecture**
   - Shows `create_multi_app()` usage
   - Multiple apps mounted with path prefixes
   - Shared authentication across all apps
   - Ticket endpoint available on parent app

2. **Ticket Exchange Flow**
   - Visual step-by-step demonstration in both apps
   - Shows JWT → Ticket → WebSocket connection
   - Displays ticket value and expiration
   - Works across multiple child apps

3. **Real-Time Broadcasting**
   - Chat app: Send message via REST API, broadcast via WebSocket
   - Notifications app: Create notification via REST API, broadcast via WebSocket
   - All connected clients receive updates instantly
   - Demonstrates app-level isolation (chat messages don't appear in notifications app)

4. **Minimal MDB-Engine Usage**
   - Only uses `get_scoped_db()` for database access
   - No AI, embeddings, or complex features
   - Focus on WebSocket functionality

5. **Security**
   - Shared auth mode with JWT cookies
   - Ticket-based WebSocket authentication (consistent across all apps)
   - CSRF protection via Origin validation
   - App-level isolation for WebSocket connections

## Testing

1. Start the app (Docker Compose or uvicorn)
2. Open http://localhost:8000
3. Register a new user
4. Navigate to `/chat-app`:
   - Click "Get Ticket & Connect"
   - Send messages and see them broadcast
5. Navigate to `/notifications-app`:
   - Click "Get Ticket & Connect"
   - Create notifications and see them broadcast
6. Open multiple browser tabs to see real-time updates across all connected clients

## Troubleshooting

### WebSocket Connection Fails

- **Check authentication**: Make sure you're logged in (JWT cookie set)
- **Check ticket**: Ticket expires in 10 seconds - get a new one if expired
- **Check console**: Browser console shows WebSocket connection errors
- **Check server logs**: Server logs show ticket validation errors

### Ticket Expired

- Tickets expire after 10 seconds
- Get a new ticket by clicking "Get Ticket & Connect" again
- Tickets are single-use - each WebSocket connection needs a new ticket

### Messages Not Broadcasting

- Make sure WebSocket is connected (status badge should be green)
- Check server logs for broadcasting errors
- Verify message was created successfully (check REST API response)

## Learn More

- [WebSocket Security Guide](../../docs/guides/WEBSOCKET_SECURITY_ELEGANT_SOLUTION.md)
- [Multi-App Guide](../../docs/guides/MULTI_APP_GUIDE.md)
- [SSO Multi-App Setup](../../docs/guides/SSO_MULTI_APP_SETUP.md)
- [WebSocket Routing README](../../mdb_engine/routing/README.md)
