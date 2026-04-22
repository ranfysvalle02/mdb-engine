"""Event action: enqueue a notification when a booking is requested.

Fires on `after_create` for the `appointments` collection. Mirrors the
lead-notification handler exactly — only the template name changes.
"""

from __future__ import annotations

from mdb_engine.actions import ActionContext

__trigger__ = "event"
__event__ = "after_create"
__collection__ = "appointments"


async def handler(ctx: ActionContext) -> None:
    appt = ctx.event_doc
    if not appt:
        return

    channel = str(appt.get("channel") or "email")
    contact = str(appt.get("contact") or "")

    db = await ctx.get_db()
    await db.outbox.insert_one(
        {
            "channel": channel,
            "to": contact,
            "template": "booking_requested",
            "payload": {
                "appointment_id": str(appt.get("_id") or ""),
                "service_id": appt.get("service_id"),
                "scheduled_at": appt.get("scheduled_at"),
                "notes": appt.get("notes"),
            },
            "status": "pending",
            "attempts": 0,
        }
    )
