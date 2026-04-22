"""Event action: enqueue a notification when a new lead is submitted.

Fires on `after_create` for the `leads` collection. Reads the lead document
from `ctx.event_doc` and appends a single row to the `outbox` collection.

No external I/O happens here. The outbox is the boundary between the app
and whatever real provider (Resend / Twilio / Meta WhatsApp / SMTP / etc.)
you later wire into `outbox-dispatch.py`. This keeps the write path fast
and resilient: if a provider is down, leads still land in the DB and the
dispatcher retries on its own schedule.
"""

from __future__ import annotations

from mdb_engine.actions import ActionContext

__trigger__ = "event"
__event__ = "after_create"
__collection__ = "leads"


async def handler(ctx: ActionContext) -> None:
    lead = ctx.event_doc
    if not lead:
        return

    channel = str(lead.get("channel") or "email")
    contact = str(lead.get("contact") or "")

    db = await ctx.get_db()
    await db.outbox.insert_one(
        {
            "channel": channel,
            "to": contact,
            "template": "lead_received",
            "payload": {
                "lead_id": str(lead.get("_id") or ""),
                "name": lead.get("name"),
                "message": lead.get("message"),
                "source_service_id": lead.get("source_service_id"),
            },
            "status": "pending",
            "attempts": 0,
        }
    )
