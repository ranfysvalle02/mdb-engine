#!/usr/bin/env python3
"""
Semantic Text Splitting and Embedding Example

This example demonstrates the new EmbeddingService that combines:
1. Semantic text splitting (Rust-based, fast and accurate)
2. Embedding generation (via mem0, provider-agnostic)
3. MongoDB storage (structured document format)

The service is configured via manifest.json embedding_config section.
"""

import asyncio
import os
from pathlib import Path

from mdb_engine import RuntimeEngine
from mdb_engine.embeddings import EmbeddingService


async def main():
    """Main example function"""
    print("🚀 Semantic Text Splitting and Embedding Example")
    print("=" * 60)
    
    # Get MongoDB connection from environment
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
        manifest_path = Path("/app/manifest.json")
        if not manifest_path.exists():
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
        
        # Get embedding config from manifest
        embedding_config = manifest.get("embedding_config", {})
        if not embedding_config.get("enabled", False):
            print("⚠️  Embedding service not enabled in manifest.json")
            print("   Add 'embedding_config' section with 'enabled: true' to enable it.")
            return
        
        # Get memory service (EmbeddingService uses mem0 from memory service)
        memory_service = engine.get_memory_service("vector_hacking")
        if not memory_service:
            print("⚠️  Memory service not enabled in manifest.json")
            print("   Embedding service requires memory_config.enabled=true in manifest.json.")
            return
        
        # Initialize Embedding Service (uses mem0 from memory service)
        print("🔧 Initializing Embedding Service...")
        from mdb_engine.embeddings import get_embedding_service
        embedding_service = get_embedding_service()
        print("✅ Embedding Service initialized\n")
        
        # Get a scoped database for the app
        db = engine.get_scoped_db("vector_hacking")
        
        # ============================================
        # Example: Process and Store Text
        # ============================================
        print("📝 Processing and storing text with embeddings...")
        print("-" * 60)
        
        # Sample long text (repeated to ensure multiple chunks)
        long_text = """
        Semantic search relies on vector embeddings to understand the context of data.
        Traditional keyword search looks for exact matches, but vector search looks for conceptual similarity.
        
        Rust is a systems programming language that runs blazingly fast, prevents segfaults, and guarantees thread safety.
        By wrapping a Rust core in Python, we get the developer experience of Python with the performance of Rust.
        
        MongoDB is a NoSQL database that now supports Vector Search via Atlas, allowing you to build
        RAG (Retrieval Augmented Generation) applications directly on your operational data.
        
        The semantic-text-splitter library uses Rust for token counting, ensuring chunks never exceed
        model limits while preserving semantic boundaries. This is crucial for maintaining context
        in RAG applications.
        
        The EmbeddingService supports OpenAI and AzureOpenAI embeddings,
        auto-detected from environment variables.
        """ * 10  # Repeat to generate multiple chunks
        
        # Process and store
        result = await embedding_service.process_and_store(
            text_content=long_text,
            source_id="doc_101",
            collection=db.embeddings,
            max_tokens=embedding_config.get("max_tokens_per_chunk", 1000),
            embedding_model=embedding_config.get("default_embedding_model")
        )
        
        print(f"✅ Created {result['chunks_created']} chunks")
        print(f"✅ Inserted {result['documents_inserted']} documents")
        print(f"✅ Source ID: {result['source_id']}\n")
        
        # ============================================
        # Example: Query Stored Embeddings
        # ============================================
        print("🔍 Querying stored embeddings...")
        print("-" * 60)
        
        # Find all chunks for this source
        chunks = await db.embeddings.find(
            {"source_id": "doc_101"}
        ).sort("chunk_index", 1).to_list(length=None)
        
        print(f"✅ Found {len(chunks)} chunks for source 'doc_101'")
        
        for chunk in chunks[:3]:  # Show first 3
            print(f"\n   Chunk {chunk['chunk_index']}:")
            print(f"   Text: {chunk['text'][:100]}...")
            print(f"   Embedding dimensions: {len(chunk['embedding'])}")
            print(f"   Model: {chunk['metadata'].get('model', 'unknown')}")
        
        if len(chunks) > 3:
            print(f"\n   ... and {len(chunks) - 3} more chunks")
        
        print()
        
        # ============================================
        # Example: Process Text Without Storing
        # ============================================
        print("📝 Processing text without storing...")
        print("-" * 60)
        
        sample_text = """
        This is a shorter text that will be chunked and embedded.
        The service will return the chunks with their embeddings,
        but won't store them in the database.
        """ * 5
        
        results = await embedding_service.process_text(
            text_content=sample_text,
            max_tokens=500
        )
        
        print(f"✅ Processed {len(results)} chunks")
        for i, result in enumerate(results):
            print(f"   Chunk {i}: {len(result['text'])} chars, "
                  f"{len(result['embedding'])} dimensions")
        
        print()
        
        # ============================================
        # Summary
        # ============================================
        print("💡 What happened:")
        print("   1. Text was split semantically using Rust-based semantic-text-splitter")
        print("   2. Each chunk was embedded using OpenAI/AzureOpenAI (auto-detected)")
        print("   3. Documents were stored in MongoDB with proper structure")
        print("   4. All configuration came from manifest.json")
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

