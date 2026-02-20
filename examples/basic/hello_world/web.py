"""
Hello World — the simplest possible mdb-engine app.

No manifest file required. Just run:
    pip install mdb-engine fastapi uvicorn
    uvicorn web:app --reload

Requires MongoDB running on localhost:27017 (or set MDB_MONGO_URI env var).
"""

from fastapi import Depends

from mdb_engine import quickstart
from mdb_engine.dependencies import get_scoped_db

app = quickstart("hello")


@app.get("/")
async def index():
    return {"message": "Hello from mdb-engine!"}


@app.post("/items")
async def create_item(item: dict, db=Depends(get_scoped_db)):
    result = await db.items.insert_one(item)
    return {"id": str(result.inserted_id)}


@app.get("/items")
async def list_items(db=Depends(get_scoped_db)):
    items = await db.items.find({}).to_list(length=50)
    for item in items:
        item["_id"] = str(item["_id"])
    return {"items": items}
