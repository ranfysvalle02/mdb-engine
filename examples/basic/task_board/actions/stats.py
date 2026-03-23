"""HTTP action: return task counts by status + archived total."""

__trigger__ = "http"
__method__ = "GET"

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    db = await ctx.get_db()
    tasks_col = db[f"{ctx.slug}_tasks"]
    archive_col = db[f"{ctx.slug}_archive"]

    todo = await tasks_col.count_documents({"status": "todo"})
    in_progress = await tasks_col.count_documents({"status": "in_progress"})
    done = await tasks_col.count_documents({"status": "done"})
    archived = await archive_col.count_documents({})

    return ctx.json_response({
        "todo": todo,
        "in_progress": in_progress,
        "done": done,
        "archived": archived,
        "total": todo + in_progress + done,
    })
