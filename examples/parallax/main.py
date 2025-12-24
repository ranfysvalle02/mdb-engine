#!/usr/bin/env python3
"""
Parallax Example for MDB_ENGINE

This example demonstrates:
- LLM integration with dual-agent analysis
- Parallax relevance and technical analysis
- Tech news monitoring with watchlist filtering
- Basic CRUD operations with automatic app scoping
"""
import asyncio
import os
from pathlib import Path
from datetime import datetime

from mdb_engine import MongoDBEngine


async def main():
    """Main example function"""
    print("🚀 Initializing Parallax with MDB_ENGINE...")
    
    # Get MongoDB connection from environment
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true"
    )
    db_name = os.getenv("MONGO_DB_NAME", "parallax_db")
    
    # Mask password in logs
    safe_uri = mongo_uri.replace("password", "***") if "password" in mongo_uri else mongo_uri
    print(f"📡 Connecting to MongoDB: {safe_uri}")
    print(f"📦 Database: {db_name}\n")
    
    # Initialize the MongoDB Engine
    engine = MongoDBEngine(
        mongo_uri=mongo_uri,
        db_name=db_name
    )
    
    try:
        # Connect to MongoDB
        await engine.initialize()
        print("✅ Engine initialized successfully")
        
        # Load and register the app manifest
        manifest_path = Path("/app/manifest.json")
        if not manifest_path.exists():
            # Fallback for local development
            manifest_path = Path(__file__).parent / "manifest.json"
            if not manifest_path.exists():
                print(f"❌ Manifest not found. Tried: /app/manifest.json and {manifest_path}")
                return
        
        manifest = await engine.load_manifest(manifest_path)
        success = await engine.register_app(manifest, create_indexes=True)
        
        if not success:
            print("❌ Failed to register app")
            return
        
        print(f"✅ App '{manifest['slug']}' registered successfully\n")
        
        # Get a scoped database for the app
        db = engine.get_scoped_db("parallax")
        
        # ============================================
        # CREATE: Create a sample chat session
        # ============================================
        print("📝 Creating sample chat session...")
        
        session = {
            "user_id": "demo_user",
            "title": "Sample Conversation",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        session_result = await db.chat_sessions.insert_one(session)
        session_id = session_result.inserted_id
        print(f"✅ Created chat session: {session_id}")
        
        # ============================================
        # CREATE: Add sample messages
        # ============================================
        print("💬 Adding sample messages...")
        
        messages = [
            {
                "session_id": session_id,
                "role": "user",
                "content": "Hello! What can you help me with?",
                "created_at": datetime.utcnow()
            },
            {
                "session_id": session_id,
                "role": "assistant",
                "content": "Hello! I'm Parallax, a GitHub repository intelligence tool built with MDB_ENGINE. I search GitHub repositories (with AGENTS.md or LLMs.md files) and analyze them from Relevance and Technical perspectives based on your watchlist keywords!",
                "created_at": datetime.utcnow()
            }
        ]
        
        for message in messages:
            result = await db.messages.insert_one(message)
            print(f"✅ Created message: {message['role']} - {message['content'][:50]}...")
        
        print()
        
        # ============================================
        # READ: Query the data
        # ============================================
        print("🔍 Querying data...")
        
        # Find all sessions
        all_sessions = await db.chat_sessions.find({}).to_list(length=10)
        print(f"✅ Found {len(all_sessions)} chat sessions")
        
        # Find messages for the session
        session_messages = await db.messages.find({"session_id": session_id}).sort("created_at", 1).to_list(length=100)
        print(f"✅ Found {len(session_messages)} messages in session")
        
        for msg in session_messages:
            print(f"   - {msg['role']}: {msg['content'][:60]}...")
        
        print()
        
        # ============================================
        # Health Check
        # ============================================
        print("📊 Health Status:")
        health = await engine.get_health_status()
        print(f"   - Status: {health['status']}")
        
        # Extract engine health details
        engine_check = next((c for c in health['checks'] if c.get('name') == 'engine'), None)
        if engine_check and engine_check.get('details'):
            details = engine_check['details']
            print(f"   - App Count: {details.get('app_count', 0)}")
            print(f"   - Initialized: {details.get('initialized', False)}")
        
        print()
        
        # ============================================
        # Understanding What Happened
        # ============================================
        print("💡 What happened behind the scenes:")
        print("   - All documents were automatically tagged with app_id='parallax'")
        print("   - All queries were automatically filtered by app_id")
        print("   - Indexes were created automatically from the manifest")
        print("   - LLM configuration is ready for multi-agent orchestration")
        print("   - You never had to think about app isolation - it just works!")
        print()
        
        print("✅ Example completed successfully!")
        
    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError, FileNotFoundError) as e:
        # Type 2: Recoverable - CLI script error, print and exit gracefully
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        await engine.shutdown()
        print("\n🧹 Cleaned up and shut down")


if __name__ == "__main__":
    asyncio.run(main())

