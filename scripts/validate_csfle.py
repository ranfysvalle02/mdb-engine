#!/usr/bin/env python3
"""
CSFLE Validation Script

Validates that Client-Side Field Level Encryption (CSFLE) is properly configured
and working. Can optionally test encryption by writing/reading encrypted data.

Usage:
    # Check status only
    python scripts/validate_csfle.py

    # Test encryption with actual data
    python scripts/validate_csfle.py --test

    # With docker run
    docker run --rm \
        -e MONGODB_URI="mongodb://admin:password@mongodb:27017/?authSource=admin" \
        -e MONGODB_DB="oblivio_apps" \
        -e MDB_CSFLE_LOCAL_KEY="${MDB_CSFLE_LOCAL_KEY}" \
        --network oblivio_apps_network \
        <image> \
        python /app/scripts/validate_csfle.py --test
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from mdb_engine import MongoDBEngine
    from mdb_engine.core.csfle import get_csfle_status
    from mdb_engine.core.engine import build_csfle_config_from_manifest
except ImportError as e:
    print(f"❌ Error importing mdb_engine: {e}")
    print("Make sure mdb_engine is installed and in PYTHONPATH")
    sys.exit(1)


def print_status(status: dict[str, Any]) -> None:
    """Print formatted CSFLE status."""
    print("\n" + "=" * 60)
    print("CSFLE Status Check")
    print("=" * 60)

    checks = [
        ("PyMongo Encryption Support", status.get("pymongo_encryption", False)),
        ("crypt_shared Library Path", status.get("crypt_shared_path", "Not set")),
        ("crypt_shared Library Exists", status.get("crypt_shared_exists", False)),
        ("Local Key Configured", status.get("local_key_configured", False)),
    ]

    all_passed = True
    for name, value in checks:
        if isinstance(value, bool):
            icon = "✅" if value else "❌"
            print(f"{icon} {name}: {value}")
            if not value:
                all_passed = False
        else:
            print(f"ℹ️  {name}: {value}")
            if value == "Not set":
                all_passed = False

    print("=" * 60)

    if status.get("available", False) and all_passed:
        print("✅ CSFLE is properly configured!")
        return True
    else:
        print("❌ CSFLE configuration has issues")
        return False


def test_encryption(manifest_path: Path, mongo_uri: str, db_name: str) -> bool:
    """Test encryption by writing and reading encrypted data."""
    import asyncio

    print("\n" + "=" * 60)
    print("CSFLE Encryption Test")
    print("=" * 60)

    # Load manifest
    if not manifest_path.exists():
        print(f"❌ Manifest not found: {manifest_path}")
        return False

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Build CSFLE config
    csfle_config = build_csfle_config_from_manifest(manifest)
    if not csfle_config or not csfle_config.enabled:
        print("❌ CSFLE not enabled in manifest")
        return False

    print(f"✅ CSFLE config loaded: {csfle_config}")
    print(f"   Encrypted collections: {list(csfle_config.encrypted_collections.keys())}")

    async def run_test() -> bool:
        """Run the async test operations."""
        engine = None
        non_csfle_client = None
        doc_id = None

        try:
            # Create engine with CSFLE
            engine = MongoDBEngine(
                mongo_uri=mongo_uri,
                db_name=db_name,
                csfle_config=csfle_config,
            )

            # Initialize engine (this sets up CSFLE)
            await engine.initialize()

            print("✅ Engine initialized with CSFLE")

            # Get the encrypted collection
            app_slug = manifest.get("slug", "app")
            memory_config = manifest.get("memory_config", {})
            collection_name = memory_config.get("collection_name", "memories")
            full_collection_name = f"{app_slug}_{collection_name}"

            print(f"   Testing collection: {full_collection_name}")

            # Write test data
            test_data = {
                "user_id": "test_user_123",
                "content": "This is a secret message that should be encrypted! 🔐",
                "text": "Another sensitive field",
                "created_at": "2024-01-01T00:00:00Z",
                "importance": 0.5,
            }

            print("\n📝 Writing test data...")
            print(f"   Original content: {test_data['content']}")

            # Insert via engine's connection (which has CSFLE enabled)
            db = engine._connection_manager.mongo_db  # noqa: SLF001
            collection = db[full_collection_name]

            result = await collection.insert_one(test_data)
            doc_id = result.inserted_id
            print(f"✅ Document inserted: {doc_id}")

            # Read back via CSFLE-enabled client (should decrypt automatically)
            print("\n📖 Reading back via CSFLE client (should decrypt)...")
            doc = await collection.find_one({"_id": doc_id})

            if doc and doc.get("content") == test_data["content"]:
                print(f"✅ Content decrypted correctly: {doc['content']}")
            else:
                print("❌ Content mismatch!")
                print(f"   Expected: {test_data['content']}")
                print(f"   Got: {doc.get('content') if doc else 'None'}")
                return False

            # Read via non-CSFLE client (should see encrypted data)
            print("\n🔍 Reading via non-CSFLE client (should see encrypted data)...")
            from pymongo import MongoClient

            # Parse URI to get connection details
            non_csfle_client = MongoClient(mongo_uri)
            non_csfle_db = non_csfle_client[db_name]
            non_csfle_collection = non_csfle_db[full_collection_name]

            raw_doc = non_csfle_collection.find_one({"_id": doc_id})

            if raw_doc:
                raw_content = raw_doc.get("content")
                # Encrypted content should be Binary type or look encrypted
                if isinstance(raw_content, bytes) or (isinstance(raw_content, str) and len(raw_content) > 100):
                    print(f"✅ Content is encrypted in database (type: {type(raw_content).__name__})")
                    print(f"   Encrypted value length: {len(str(raw_content))}")
                else:
                    print(f"⚠️  Content may not be encrypted (type: {type(raw_content).__name__})")
                    print(f"   Value: {raw_content}")

            # Cleanup
            print("\n🧹 Cleaning up test data...")
            await collection.delete_one({"_id": doc_id})
            doc_id = None  # Mark as cleaned
            print("✅ Test data removed")

            print("\n" + "=" * 60)
            print("✅ Encryption test passed!")
            print("=" * 60)
            return True

        except (
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            ConnectionError,
            OSError,
            RuntimeError,
        ) as e:
            print(f"\n❌ Encryption test failed: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            # Cleanup resources
            if engine:
                try:
                    await engine.shutdown()
                except (RuntimeError, AttributeError, ConnectionError):
                    pass
            if non_csfle_client:
                try:
                    non_csfle_client.close()
                except (ConnectionError, AttributeError):
                    pass

    # Run the async test with a single event loop
    return asyncio.run(run_test())


def main():
    parser = argparse.ArgumentParser(description="Validate CSFLE configuration")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test encryption by writing/reading encrypted data",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("apps/sso-app-3/manifest.json"),
        help="Path to manifest.json file",
    )
    parser.add_argument(
        "--mongo-uri",
        type=str,
        default=os.getenv("MONGODB_URI", os.getenv("MONGO_URI", "mongodb://localhost:27017/")),
        help="MongoDB connection URI",
    )
    parser.add_argument(
        "--db-name",
        type=str,
        default=os.getenv("MONGODB_DB", os.getenv("MONGO_DB_NAME", "oblivio_apps")),
        help="Database name",
    )

    args = parser.parse_args()

    # Check status
    status = get_csfle_status()
    status_ok = print_status(status)

    if not status_ok:
        print("\n⚠️  Fix CSFLE configuration issues before testing encryption")
        sys.exit(1)

    # Test encryption if requested
    if args.test:
        test_ok = test_encryption(args.manifest, args.mongo_uri, args.db_name)
        sys.exit(0 if test_ok else 1)
    else:
        print("\n💡 Tip: Run with --test to verify encryption is working")
        sys.exit(0)


if __name__ == "__main__":
    main()
