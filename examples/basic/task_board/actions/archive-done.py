"""Scheduled action: move completed tasks to the archive collection."""

__trigger__ = "schedule"
__interval_seconds__ = 86400

from datetime import datetime, timezone

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    db = await ctx.get_db()
    tasks_col = db[f"{ctx.slug}_tasks"]
    archive_col = db[f"{ctx.slug}_archive"]

    done = await tasks_col.find({"status": "done"}).to_list(length=500)
    if not done:
        return

    ids = [t["_id"] for t in done]
    now = datetime.now(tz=timezone.utc).isoformat()
    for t in done:
        t.pop("_id", None)
        t["archived_at"] = now

    await archive_col.insert_many(done)
    await tasks_col.delete_many({"_id": {"$in": ids}})
