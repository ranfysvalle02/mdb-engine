# Hello World Example

A simple example demonstrating MDB_RUNTIME with a single-app setup.

This example shows:
- How to initialize the runtime engine
- How to create and register an app manifest
- Basic CRUD operations with automatic app scoping
- How to use the scoped database wrapper

## Prerequisites

- Python 3.8+
- Docker and Docker Compose (for containerized setup)
- OR MongoDB running locally (for local setup)
- MDB_RUNTIME installed

## Setup

### Using Docker Compose (Recommended)

Everything runs in Docker - MongoDB, the application, and optional services.

**Just run:**
```bash
docker-compose up
```

That's it! The Docker Compose setup will:
1. Build the application Docker image using multi-stage build (installs MDB_RUNTIME automatically)
2. Start MongoDB with authentication and health checks
3. Start the **web application** on http://localhost:8000
4. Start MongoDB Express (Web UI) - optional, use `--profile ui`
5. Show you all the output

**Access the Web UI:**
- 🌐 **Web Application**: http://localhost:8000
- 🔐 **Login Credentials**: 
  - Email: `demo@demo.com`
  - Password: `password123`

**With optional services:**
```bash
# Include MongoDB Express UI
docker-compose --profile ui up
```

**View logs:**
```bash
# All services
docker-compose logs -f

# Just the app
docker-compose logs -f app

# Just MongoDB
docker-compose logs -f mongodb
```

**Stop everything:**
```bash
docker-compose down
```

**Rebuild after code changes:**
```bash
docker-compose up --build
```

**Production mode:**
```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**What gets started:**
- ✅ **App Container** - Runs the hello_world example automatically
- ✅ **MongoDB** - Database on port 27017
- ✅ **MongoDB Express** - Web UI on http://localhost:8081 (optional, with `--profile ui`)

**Access the services:**
- 🌐 **Web Application**: http://localhost:8000 (login: demo@demo.com / password123)
- MongoDB: `mongodb://admin:password@localhost:27017/?authSource=admin`
- MongoDB Express UI: http://localhost:8081 (login: admin/admin, optional)

**Run in detached mode:**
```bash
docker-compose up -d
docker-compose logs -f app  # Follow app logs
```

**Clean up (removes all data):**
```bash
docker-compose down -v
```

## Testing

After running `docker-compose up`, the app automatically runs and tests itself. You'll see the output directly in your terminal.

**To test manually:**

1. **Run the example:**
   ```bash
   docker-compose up
   ```

2. **View the output:**
   The app will automatically:
   - Connect to MongoDB
   - Register the app
   - Create sample data
   - Query the data
   - Update records
   - Display health status

3. **Check logs:**
   ```bash
   # View all logs
   docker-compose logs -f
   
   # View just the app logs
   docker-compose logs -f app
   ```

4. **Run again (after stopping):**
   ```bash
   docker-compose down
   docker-compose up
   ```

5. **Test with MongoDB Express UI:**
   ```bash
   docker-compose --profile ui up
   ```
   Then visit http://localhost:8081 to browse the database and see the created data.

6. **Verify data in MongoDB:**
   ```bash
   # Connect to MongoDB container
   docker-compose exec mongodb mongosh -u admin -p password --authenticationDatabase admin
   
   # In mongosh:
   use hello_world_db
   db.greetings.find()
```

## What This Example Does

### Web Application Features

The hello_world example includes a **full-featured web application** with:

1. **🔐 Authentication** - Login/logout with JWT tokens
   - Demo user: `demo@demo.com` / `password123`
   - Secure session management with HTTP-only cookies

2. **📊 Dashboard** - Beautiful, modern UI showing:
   - Real-time greeting management (Create, Read, Delete)
   - System health status
   - Statistics (total greetings, languages)
   - Multi-language support

3. **🎨 Modern UI/UX** - Responsive design with:
   - Gradient backgrounds
   - Smooth animations
   - Mobile-friendly layout
   - Real-time updates

### Backend Features

1. **Initializes the Runtime Engine** - Connects to MongoDB and sets up the runtime
2. **Registers the App** - Loads the manifest and registers the "hello_world" app
3. **Creates Data** - Inserts sample documents (seeded on startup)
4. **Queries Data** - Demonstrates find operations with automatic app scoping
5. **Updates Data** - Shows how updates work with scoped database
6. **Shows Health Status** - Displays engine health information via API

## Expected Output

```
🚀 Initializing MDB_RUNTIME...
✅ Engine initialized successfully
✅ App 'hello_world' registered successfully

📝 Creating sample data...
✅ Created greeting: Hello, World!
✅ Created greeting: Hello, MDB_RUNTIME!
✅ Created greeting: Hello, Python!

🔍 Querying data...
✅ Found 3 greetings
   - Hello, World! (language: en)
   - Hello, MDB_RUNTIME! (language: en)
   - Hello, Python! (language: en)

🔍 Finding English greetings...
✅ Found 3 English greetings

✏️  Updating greeting...
✅ Updated greeting: Hello, Updated World!

🔍 Verifying update...
✅ Found updated greeting: Hello, Updated World!

📊 Health Status:
   - Status: connected
   - App Count: 1
   - Initialized: True

✅ Example completed successfully!
```

## Understanding the Code

### Manifest (`manifest.json`)

The manifest defines your app configuration:
- `slug`: Unique identifier for your app
- `name`: Human-readable name
- `status`: App status (active, draft, etc.)
- `managed_indexes`: Indexes to create automatically

### App Scoping

Notice that when you insert documents, you don't specify `app_id`. MDB_RUNTIME automatically adds it:

```python
# You write:
await db.greetings.insert_one({"message": "Hello, World!", "language": "en"})

# MDB_RUNTIME stores:
{
  "message": "Hello, World!",
  "language": "en",
  "app_id": "hello_world"  # ← Added automatically
}
```

### Automatic Filtering

When you query, MDB_RUNTIME automatically filters by `app_id`:

```python
# You write:
greetings = await db.greetings.find({"language": "en"}).to_list(length=10)

# MDB_RUNTIME executes:
db.greetings.find({
  "$and": [
    {"app_id": {"$in": ["hello_world"]}},  # ← Added automatically
    {"language": "en"}
  ]
})
```

## Docker Compose Services

The `docker-compose.yml` file includes:

The `docker-compose.yml` file includes:

### App Service
- **Container:** `hello_world_app`
- **Purpose:** Runs the hello_world example
- **Build:** Automatically builds from Dockerfile
- **Dependencies:** Waits for MongoDB to be healthy

### MongoDB
- **Port:** 27017
- **Credentials:** admin/password (change in production!)
- **Database:** hello_world_db
- **Health Check:** Enabled (app waits for this)

### MongoDB Express (Web UI)
- **URL:** http://localhost:8081
- **Credentials:** admin/admin
- **Purpose:** Browse and manage your MongoDB data visually

### Environment Variables

Docker Compose automatically loads environment variables from a `.env` file in the same directory. All configuration values in `docker-compose.yml` use environment variable substitution with sensible defaults.

**To customize configuration, create a `.env` file:**

```bash
# Create .env file from example (if available)
cp .env.example .env

# Or create your own .env file with:
APP_PORT=8000
APP_SLUG=hello_world
LOG_LEVEL=INFO
MONGO_URI=mongodb://admin:password@mongodb:27017/?authSource=admin
MONGO_DB_NAME=hello_world_db
MONGO_PORT=27017
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=password
FLASK_SECRET_KEY=your-secret-key-here
```

**All environment variables are optional** - the docker-compose.yml file provides defaults for all values. You only need to set variables you want to override.

### Customizing the Setup

1. **Modify `docker-compose.yml`** to change service configurations

2. **Modify `Dockerfile`** to change the build process

3. **Rebuild and restart:**
   ```bash
   docker-compose up --build
   ```

## Troubleshooting

### ModuleNotFoundError: No module named 'mdb_runtime'

If you see this error, the package wasn't installed correctly during the Docker build. Fix it by:

1. **Rebuild the Docker image:**
   ```bash
   docker-compose build --no-cache
   docker-compose up
   ```

2. **Or force a complete rebuild:**
   ```bash
   docker-compose down
   docker-compose build --no-cache
   docker-compose up
   ```

This error was fixed in the Dockerfile by changing from editable install (`-e`) to regular install, which works correctly when copying the virtual environment between Docker stages.

### App Won't Start

1. **Check if MongoDB is healthy:**
   ```bash
   docker-compose ps
   docker-compose logs mongodb
   ```

2. **Check app logs:**
   ```bash
   docker-compose logs app
   ```

3. **Rebuild the app:**
   ```bash
   docker-compose up --build
   ```

### MongoDB Connection Issues

The app connects to MongoDB using the service name `mongodb` (Docker networking).
If you see connection errors:

1. **Verify MongoDB is running:**
   ```bash
   docker-compose ps mongodb
   ```

2. **Check MongoDB is healthy:**
   ```bash
   docker-compose logs mongodb | grep "health"
   ```

3. **Test connection from app container:**
   ```bash
   docker-compose exec app python -c "from motor.motor_asyncio import AsyncIOMotorClient; import asyncio; asyncio.run(AsyncIOMotorClient('mongodb://admin:password@mongodb:27017/?authSource=admin').admin.command('ping'))"
   ```

### Port Conflicts

If ports are already in use, modify `docker-compose.yml`:

```yaml
services:
  mongodb:
    ports:
      - "27018:27017"  # Change host port
```

Then update `MONGO_URI` in the app service to use the new port if accessing from outside Docker.

### Rebuild After Code Changes

If you modify `mdb_runtime` or the example code:

```bash
docker-compose up --build
```

## Docker Best Practices

This example follows enterprise-grade Docker best practices:

- ✅ **Multi-stage builds** - Smaller, more secure images
- ✅ **Non-root user** - Security best practice
- ✅ **Health checks** - Proper service monitoring
- ✅ **Resource limits** - Prevent resource exhaustion
- ✅ **Layer caching** - Optimized build performance
- ✅ **Network isolation** - Secure service communication
- ✅ **Service dependencies** - Proper startup ordering

See [DOCKER_BEST_PRACTICES.md](./DOCKER_BEST_PRACTICES.md) for a comprehensive guide on:
- Why each practice matters
- How to apply them to your own applications
- Production deployment considerations
- Security hardening
- Performance optimization

## Next Steps

- Try adding more collections
- Experiment with different queries
- Add indexes to the manifest
- Check out the FastAPI example for building a real API
- Explore MongoDB Express UI: `docker-compose --profile ui up` then http://localhost:8081
- Review [DOCKER_BEST_PRACTICES.md](./DOCKER_BEST_PRACTICES.md) for production deployment

---

## Forking & Expanding: From Hello World to Real Applications

The hello_world example is designed as a **starting point** for real-world applications. Here's how to transform it into production-ready apps.

### Quick Start: Forking the Example

1. **Copy the example:**
   ```bash
   cp -r examples/hello_world my_new_app
   cd my_new_app
   ```

2. **Update the manifest:**
   - Change `slug` to your app name
   - Update `name` and `description`
   - Modify `developer_id` to your email
   - Add your collections to `managed_indexes`

3. **Rename files:**
   ```bash
   mv web.py app.py  # Or your preferred name
   ```

4. **Update Docker Compose:**
   - Change service names
   - Update environment variables
   - Adjust ports if needed

5. **Start building:**
   ```bash
   docker-compose up --build
   ```

### Real-World Application Ideas

#### 1. **Task Management / Project Management App**

**What to Change:**
- Replace `greetings` collection with `tasks`, `projects`, `users`
- Add status workflows (todo → in_progress → done)
- Implement user assignments and due dates

**Collections:**
```json
{
  "managed_indexes": {
    "tasks": [
      {"keys": {"project_id": 1, "status": 1, "due_date": 1}},
      {"keys": {"assigned_to": 1, "status": 1}}
    ],
    "projects": [
      {"keys": {"owner_id": 1, "created_at": -1}}
    ]
  }
}
```

**WebSocket Use Cases:**
- Real-time task updates when team members change status
- Live notifications for new assignments
- Collaborative editing indicators

**Example Expansion:**
```python
# Add to web.py
@app.post("/api/tasks")
async def create_task(task_data: dict, user: dict = Depends(get_current_user)):
    db = get_db()
    task = {
        **task_data,
        "created_by": user["email"],
        "created_at": datetime.utcnow(),
        "status": "todo"
    }
    result = await db.tasks.insert_one(task)
    
    # Broadcast via WebSocket
    await broadcast_to_app("task_manager", {
        "type": "task_created",
        "data": {**task, "_id": str(result.inserted_id)}
    })
    return {"id": str(result.inserted_id)}
```

#### 2. **E-Commerce / Product Catalog**

**What to Change:**
- Replace `greetings` with `products`, `categories`, `orders`, `cart`
- Add inventory management
- Implement search with MongoDB Atlas Search

**Collections:**
```json
{
  "managed_indexes": {
    "products": [
      {"keys": {"category": 1, "price": 1}},
      {"keys": {"name": "text", "description": "text"}},
      {"keys": {"in_stock": 1, "created_at": -1}}
    ],
    "orders": [
      {"keys": {"user_id": 1, "status": 1, "created_at": -1}},
      {"keys": {"status": 1, "payment_status": 1}}
    ]
  }
}
```

**WebSocket Use Cases:**
- Real-time inventory updates
- Order status notifications
- Live cart synchronization across devices

**Example Expansion:**
```python
@app.post("/api/orders")
async def create_order(order_data: dict, user: dict = Depends(get_current_user)):
    db = get_db()
    
    # Create order
    order = {
        **order_data,
        "user_id": user["user_id"],
        "status": "pending",
        "created_at": datetime.utcnow()
    }
    result = await db.orders.insert_one(order)
    
    # Update inventory
    for item in order_data["items"]:
        await db.products.update_one(
            {"_id": ObjectId(item["product_id"])},
            {"$inc": {"in_stock": -item["quantity"]}}
        )
    
    # Broadcast to user
    await broadcast_to_app("ecommerce", {
        "type": "order_created",
        "data": {**order, "_id": str(result.inserted_id)}
    }, user_id=user["user_id"])
    
    return {"id": str(result.inserted_id)}
```

#### 3. **Social Media / Content Platform**

**What to Change:**
- Replace `greetings` with `posts`, `comments`, `users`, `follows`
- Add feed algorithms
- Implement real-time engagement metrics

**Collections:**
```json
{
  "managed_indexes": {
    "posts": [
      {"keys": {"author_id": 1, "created_at": -1}},
      {"keys": {"tags": 1, "created_at": -1}},
      {"keys": {"likes_count": -1, "created_at": -1}}
    ],
    "comments": [
      {"keys": {"post_id": 1, "created_at": 1}},
      {"keys": {"author_id": 1, "created_at": -1}}
    ],
    "follows": [
      {"keys": {"follower_id": 1, "following_id": 1}, "unique": true}
    ]
  }
}
```

**WebSocket Use Cases:**
- Real-time feed updates
- Live comment threads
- Instant like/engagement notifications
- Online presence indicators

**Example Expansion:**
```python
@app.post("/api/posts/{post_id}/like")
async def like_post(post_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    
    # Toggle like
    like = await db.likes.find_one({
        "post_id": ObjectId(post_id),
        "user_id": user["user_id"]
    })
    
    if like:
        await db.likes.delete_one({"_id": like["_id"]})
        action = "unliked"
    else:
        await db.likes.insert_one({
            "post_id": ObjectId(post_id),
            "user_id": user["user_id"],
            "created_at": datetime.utcnow()
        })
        action = "liked"
    
    # Update post like count
    count = await db.likes.count_documents({"post_id": ObjectId(post_id)})
    await db.posts.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": {"likes_count": count}}
    )
    
    # Broadcast to all viewers
    await broadcast_to_app("social_platform", {
        "type": "post_liked",
        "data": {
            "post_id": post_id,
            "user_id": user["user_id"],
            "action": action,
            "likes_count": count
        }
    })
    
    return {"action": action, "likes_count": count}
```

#### 4. **Chat / Messaging Application**

**What to Change:**
- Replace `greetings` with `conversations`, `messages`, `users`
- Add typing indicators
- Implement message delivery status

**Collections:**
```json
{
  "managed_indexes": {
    "conversations": [
      {"keys": {"participants": 1, "last_message_at": -1}},
      {"keys": {"type": 1, "created_at": -1}}
    ],
    "messages": [
      {"keys": {"conversation_id": 1, "created_at": 1}},
      {"keys": {"sender_id": 1, "created_at": -1}}
    ]
  }
}
```

**WebSocket Use Cases:**
- Real-time message delivery
- Typing indicators
- Read receipts
- Online/offline status
- Message reactions

**Example Expansion:**
```python
# Register message handler for chat
register_message_handler("chat_app", "realtime", handle_chat_message)

async def handle_chat_message(websocket, message):
    msg_type = message.get("type")
    
    if msg_type == "send_message":
        db = engine.get_scoped_db("chat_app")
        
        # Save message
        msg = {
            "conversation_id": ObjectId(message["conversation_id"]),
            "sender_id": message["sender_id"],
            "content": message["content"],
            "created_at": datetime.utcnow(),
            "status": "sent"
        }
        result = await db.messages.insert_one(msg)
        
        # Get conversation participants
        conv = await db.conversations.find_one({
            "_id": ObjectId(message["conversation_id"])
        })
        
        # Broadcast to all participants
        for participant_id in conv["participants"]:
            await broadcast_to_app("chat_app", {
                "type": "new_message",
                "data": {**msg, "_id": str(result.inserted_id)}
            }, user_id=participant_id)
    
    elif msg_type == "typing":
        # Broadcast typing indicator
        await broadcast_to_app("chat_app", {
            "type": "user_typing",
            "data": {
                "conversation_id": message["conversation_id"],
                "user_id": message["user_id"],
                "is_typing": message.get("is_typing", True)
            }
        })
```

#### 5. **Analytics / Dashboard Platform**

**What to Change:**
- Replace `greetings` with `events`, `metrics`, `dashboards`, `widgets`
- Add time-series data aggregation
- Implement real-time metric streaming

**Collections:**
```json
{
  "managed_indexes": {
    "events": [
      {"keys": {"event_type": 1, "timestamp": -1}},
      {"keys": {"user_id": 1, "timestamp": -1}}
    ],
    "metrics": [
      {"keys": {"metric_name": 1, "timestamp": -1}},
      {"keys": {"dashboard_id": 1, "updated_at": -1}}
    ]
  }
}
```

**WebSocket Use Cases:**
- Real-time metric updates
- Live dashboard refreshes
- Alert notifications

### Common Patterns to Implement

#### Pattern 1: User Management

```python
# Add user registration
@app.post("/api/users/register")
async def register_user(user_data: dict):
    db = get_db()
    
    # Check if user exists
    existing = await db.users.find_one({"email": user_data["email"]})
    if existing:
        raise HTTPException(400, "User already exists")
    
    # Hash password
    password_hash = bcrypt.hashpw(
        user_data["password"].encode("utf-8"),
        bcrypt.gensalt()
    )
    
    user = {
        "email": user_data["email"],
        "password_hash": password_hash,
        "full_name": user_data.get("full_name"),
        "role": "user",
        "created_at": datetime.utcnow()
    }
    
    result = await db.users.insert_one(user)
    return {"id": str(result.inserted_id)}
```

#### Pattern 2: Pagination

```python
@app.get("/api/items")
async def list_items(
    page: int = 1,
    limit: int = 20,
    sort_by: str = "created_at",
    order: str = "desc"
):
    db = get_db()
    
    skip = (page - 1) * limit
    sort_direction = -1 if order == "desc" else 1
    
    items = await db.items.find({}) \
        .sort(sort_by, sort_direction) \
        .skip(skip) \
        .limit(limit) \
        .to_list(length=limit)
    
    total = await db.items.count_documents({})
    
    return {
        "items": items,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit
        }
    }
```

#### Pattern 3: Search with Filters

```python
@app.get("/api/products/search")
async def search_products(
    q: str = None,
    category: str = None,
    min_price: float = None,
    max_price: float = None
):
    db = get_db()
    
    query = {}
    
    if q:
        query["$text"] = {"$search": q}
    
    if category:
        query["category"] = category
    
    if min_price is not None or max_price is not None:
        query["price"] = {}
        if min_price is not None:
            query["price"]["$gte"] = min_price
        if max_price is not None:
            query["price"]["$lte"] = max_price
    
    products = await db.products.find(query).to_list(length=50)
    return {"products": products}
```

#### Pattern 4: Soft Deletes

```python
@app.delete("/api/items/{item_id}")
async def delete_item(item_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    
    # Soft delete instead of hard delete
    result = await db.items.update_one(
        {"_id": ObjectId(item_id)},
        {
            "$set": {
                "deleted_at": datetime.utcnow(),
                "deleted_by": user["user_id"]
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(404, "Item not found")
    
    # Broadcast deletion
    await broadcast_to_app("my_app", {
        "type": "item_deleted",
        "data": {"item_id": item_id}
    })
    
    return {"deleted": True}
```

### Migration Checklist

When expanding hello_world to a real app:

- [ ] **Update manifest.json**
  - [ ] Change `slug` to your app name
  - [ ] Update `name` and `description`
  - [ ] Add all collections to `managed_indexes`
  - [ ] Configure `websockets` endpoints if needed
  - [ ] Set up `auth_policy` requirements

- [ ] **Modify Data Model**
  - [ ] Replace `greetings` collection with your domain collections
  - [ ] Add relationships between collections
  - [ ] Define proper indexes for your query patterns

- [ ] **Update API Endpoints**
  - [ ] Replace greeting CRUD with your domain operations
  - [ ] Add business logic and validation
  - [ ] Implement proper error handling
  - [ ] Add pagination, filtering, search

- [ ] **Enhance Authentication**
  - [ ] Add user registration
  - [ ] Implement password reset
  - [ ] Add role-based access control
  - [ ] Configure session management

- [ ] **Add WebSocket Features**
  - [ ] Register message handlers for your use cases
  - [ ] Implement broadcasting for real-time updates
  - [ ] Add connection status indicators
  - [ ] Handle reconnection logic

- [ ] **Update UI**
  - [ ] Replace greeting UI with your domain UI
  - [ ] Add forms for creating/editing entities
  - [ ] Implement real-time updates display
  - [ ] Add proper error messages and loading states

- [ ] **Production Readiness**
  - [ ] Update environment variables
  - [ ] Configure production database
  - [ ] Set up proper secrets management
  - [ ] Add monitoring and alerting
  - [ ] Review [DOCKER_BEST_PRACTICES.md](./DOCKER_BEST_PRACTICES.md)

### Architecture Recommendations

**For Small Apps (< 10K users):**
- Single FastAPI application
- Direct MongoDB connection
- WebSocket for real-time features
- Simple file-based configuration

**For Medium Apps (10K - 100K users):**
- Add Redis for caching
- Implement background task queue (Celery/RQ)
- Add CDN for static assets
- Use MongoDB replica set

**For Large Apps (100K+ users):**
- Microservices architecture
- Message queue (RabbitMQ/Kafka)
- MongoDB sharding
- Load balancer with multiple app instances
- Separate WebSocket service if needed

### Getting Help

- **Documentation**: See main [README.md](../../README.md) for full API reference
- **Examples**: Check other examples in `examples/` directory
- **Issues**: Open an issue on GitHub for bugs or questions
- **WebSocket Guide**: See [WebSocket Appendix](../../README.md#appendix-websocket-support) in main README

### Next Steps After Forking

1. **Start Small**: Implement one feature at a time
2. **Test Incrementally**: Add tests as you build
3. **Iterate**: Use the manifest-driven approach to evolve your schema
4. **Monitor**: Use built-in observability to track performance
5. **Scale**: Add more apps when ready - the infrastructure is already there!

**Remember**: The hello_world example gives you production-ready infrastructure. Focus on your business logic, and MDB_RUNTIME handles the rest.

