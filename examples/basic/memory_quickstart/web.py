"""
Memory Quickstart — minimal AI memory app with mdb-engine.

Stores and retrieves memories using semantic search (MongoDB Atlas Vector Search).

Run:
    pip install mdb-engine fastapi uvicorn
    export OPENAI_API_KEY=sk-...
    uvicorn web:app --reload

Requires:
- MongoDB running on localhost:27017 (or set MONGODB_URI)
- An OpenAI API key (or change llm_config/embedding_config in manifest.json)
"""

import asyncio
from pathlib import Path

from fastapi import Depends

from mdb_engine import MongoDBEngine
from mdb_engine.dependencies import get_memory_service, get_scoped_db

engine = MongoDBEngine()
app = engine.create_app(slug="my_bot", manifest=Path("manifest.json"))


@app.post("/remember")
async def remember(text: str, memory=Depends(get_memory_service)):
    """Store a memory. The LLM extracts facts automatically."""
    result = await asyncio.to_thread(memory.add, messages=text, user_id="user1")
    return {"stored": len(result), "memories": result}


@app.get("/recall")
async def recall(q: str, memory=Depends(get_memory_service)):
    """Search memories by meaning (semantic search)."""
    results = await asyncio.to_thread(memory.search, query=q, user_id="user1", limit=5)
    return {"results": results}


@app.get("/memories")
async def list_all(memory=Depends(get_memory_service)):
    """List all stored memories."""
    results = await asyncio.to_thread(memory.get_all, user_id="user1")
    return {"memories": results}
