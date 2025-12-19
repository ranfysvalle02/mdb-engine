#!/usr/bin/env python3
"""
Vector Hacking Example for MDB_RUNTIME

This example demonstrates the basic usage of MDB_RUNTIME with a vector hacking demo.
It shows how to initialize the engine, register an app, and perform basic operations.
"""
import asyncio
import os
from pathlib import Path
from datetime import datetime

from mdb_runtime import RuntimeEngine


async def main():
    """Main example function"""
    print("🚀 Initializing MDB_RUNTIME for Vector Hacking Demo...")
    
    # Get MongoDB connection from environment
    # In Docker Compose, this is set automatically
    mongo_uri = os.getenv(
        "MONGO_URI", 
        "mongodb://admin:password@mongodb:27017/?authSource=admin"
    )
    db_name = os.getenv("MONGO_DB_NAME", "vector_hacking_db")
    
    # Mask password in logs
    safe_uri = mongo_uri.replace("password", "***") if "password" in mongo_uri else mongo_uri
    print(f"📡 Connecting to MongoDB: {safe_uri}")
    print(f"📦 Database: {db_name}\n")
    
    # Initialize the runtime engine
    engine = RuntimeEngine(
        mongo_uri=mongo_uri,
        db_name=db_name
    )
    
    try:
        # Connect to MongoDB
        await engine.initialize()
        print("✅ Engine initialized successfully")
        
        # Load and register the app manifest
        # Works in both Docker (/app) and local development
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
        # All operations will be automatically scoped to "vector_hacking"
        db = engine.get_scoped_db("vector_hacking")
        
        # ============================================
        # CREATE: Insert some sample data
        # ============================================
        print("📝 Creating sample data...")
        
        experiments = [
            {
                "name": "Initial Test",
                "target_text": "Be mindful",
                "status": "completed",
                "created_at": datetime.utcnow()
            },
            {
                "name": "Vector Inversion Demo",
                "target_text": "Be mindful",
                "status": "pending",
                "created_at": datetime.utcnow()
            },
        ]
        
        for experiment in experiments:
            result = await db.experiments.insert_one(experiment)
            print(f"✅ Created experiment: {experiment['name']}")
        
        print()
        
        # ============================================
        # READ: Query the data
        # ============================================
        print("🔍 Querying data...")
        
        # Find all experiments
        # Note: This automatically filters by app_id - you don't need to specify it!
        all_experiments = await db.experiments.find({}).to_list(length=10)
        print(f"✅ Found {len(all_experiments)} experiments")
        
        for experiment in all_experiments:
            print(f"   - {experiment['name']} (status: {experiment['status']})")
        
        print()
        
        # Find experiments by status
        # The app_id filter is still automatically applied
        pending_experiments = await db.experiments.find({"status": "pending"}).to_list(length=10)
        print(f"🔍 Finding pending experiments...")
        print(f"✅ Found {len(pending_experiments)} pending experiments")
        print()
        
        # ============================================
        # UPDATE: Modify existing data
        # ============================================
        print("✏️  Updating experiment...")
        
        # Update the first experiment
        if all_experiments:
            experiment_id = all_experiments[0]["_id"]
            update_result = await db.experiments.update_one(
                {"_id": experiment_id},
                {"$set": {"status": "running", "updated_at": datetime.utcnow()}}
            )
            
            if update_result.modified_count > 0:
                print("✅ Updated experiment status to 'running'")
            else:
                print("⚠️  No experiment was updated")
        
        print()
        
        # ============================================
        # READ: Verify the update
        # ============================================
        print("🔍 Verifying update...")
        
        updated_experiment = await db.experiments.find_one({"status": "running"})
        if updated_experiment:
            print(f"✅ Found updated experiment: {updated_experiment['name']}")
        else:
            print("⚠️  Updated experiment not found")
        
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
        else:
            print(f"   - App Count: 0")
            print(f"   - Initialized: False")
        print()
        
        # ============================================
        # Understanding What Happened
        # ============================================
        print("💡 What happened behind the scenes:")
        print("   - All documents were automatically tagged with app_id='vector_hacking'")
        print("   - All queries were automatically filtered by app_id")
        print("   - Indexes were created automatically from the manifest")
        print("   - You never had to think about app isolation - it just works!")
        print()
        
        print("✅ Example completed successfully!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        await engine.shutdown()
        print("\n🧹 Cleaned up and shut down")


if __name__ == "__main__":
    asyncio.run(main())

