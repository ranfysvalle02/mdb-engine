"""Event action: assign a sequential task number on create."""

__trigger__ = "event"
__event__ = "after_create"
__collection__ = "tasks"

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    doc = ctx.event_doc
    if not doc:
        return

    db = await ctx.get_db()
    count = await db[f"{ctx.slug}_tasks"].count_documents({})
    await db[f"{ctx.slug}_tasks"].update_one(
        {"_id": doc["_id"]},
        {"$set": {"number": count}},
    )
