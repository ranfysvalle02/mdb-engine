import os
from pathlib import Path
from dotenv import load_dotenv
from mem0 import Memory
from pymongo import MongoClient

# --- 1. ROBUST ENV LOADING ---
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# --- 2. CRITICAL: ENV VAR MAPPING ---
# The OpenAI SDK (used by mem0) is stubborn. It demands specific variable names 
# if arguments aren't passed explicitly. We map your Azure vars to what it wants.

# REQUIRED: Maps your AZURE version to the generic OPENAI_API_VERSION
os.environ["OPENAI_API_VERSION"] = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

# REQUIRED: Maps your AZURE endpoint to AZURE_OPENAI_ENDPOINT (standard SDK check)
os.environ["AZURE_OPENAI_ENDPOINT"] = os.getenv("AZURE_OPENAI_ENDPOINT")

# REQUIRED: Maps your AZURE key to AZURE_OPENAI_API_KEY
os.environ["AZURE_OPENAI_API_KEY"] = os.getenv("AZURE_OPENAI_API_KEY")

# --- 3. MINIMAL CONFIGURATION ---
# We act "dumb" here. We only tell mem0 the MODEL name.
# It will be forced to look at the os.environ variables above for the rest.
config = {
    "llm": {
        "provider": "azure_openai",
        "config": {
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
            "temperature": 0.1
        }
    },
    "embedder": {
        "provider": "azure_openai",
        "config": {
            "model": os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
        }
    },
    "vector_store": {
        "provider": "mongodb",
        "config": {
            "mongo_uri": os.getenv("MONGO_URI"),
            "db_name": os.getenv("MONGO_DB_NAME"),
            "collection_name": os.getenv("MONGO_COLLECTION"),
            "embedding_model_dims": 1536
        }
    }
}

def test_integration():
    print("\n--- 1. Initializing Client ---")
    try:
        m = Memory.from_config(config)
        print("✅ Client initialized successfully.")
    except (ValueError, TypeError, AttributeError, RuntimeError) as e:
        # Type 2: Recoverable - initialization failed, exit test
        print(f"❌ Initialization Failed: {e}")
        return

    user_id = "test_user_final"
    
    print(f"\n--- 2. Adding Memory for {user_id} ---")
    try:
        # Takes text -> Sends to Azure Embedding (using env vars) -> Stores in MongoDB
        result = m.add("I am testing the environment variable injection fix.", user_id=user_id)
        print(f"✅ Add Result: {result}")
    except (ValueError, TypeError, RuntimeError, ConnectionError) as e:
        # Type 2: Recoverable - add failed, exit test
        print(f"❌ Add Failed: {e}")
        return

    print(f"\n--- 3. Verifying MongoDB Data ---")
    try:
        client = MongoClient(os.getenv("MONGO_URI"))
        coll = client[os.getenv("MONGO_DB_NAME")][os.getenv("MONGO_COLLECTION")]
        count = coll.count_documents({"user_id": user_id})
        print(f"✅ MongoDB Count for {user_id}: {count}")
    except (ConnectionError, ValueError, TypeError, AttributeError) as e:
        # Type 2: Recoverable - MongoDB check failed, continue test
        print(f"❌ MongoDB Check Failed: {e}")

    print(f"\n--- 4. Retrieving Memory ---")
    try:
        search_results = m.search("What am I testing?", user_id=user_id)
        print(f"✅ Search Results: {search_results}")
    except (ValueError, TypeError, RuntimeError, ConnectionError) as e:
        # Type 2: Recoverable - search failed, continue test
        print(f"❌ Search Failed: {e}")

if __name__ == "__main__":
    test_integration()
