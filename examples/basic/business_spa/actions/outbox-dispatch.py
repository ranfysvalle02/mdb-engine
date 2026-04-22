"""Schedule action: drain the outbox every 30 seconds.

This is the ONE place where a real email / WhatsApp / SMS provider would
be called. In this demo we simply log each queued message and mark it
as `logged` so the SPA's admin view can show the full pending -> logged
progression and prove the pipeline is wired end-to-end.

Swap the HOOK block below for a real SDK call (Resend, Twilio, Meta
Cloud API, SMTP, etc.) when you productionize. Provider credentials
belong in environment variables, never in the manifest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from mdb_engine.actions import ActionContext

__trigger__ = "schedule"
__interval_seconds__ = 30

logger = logging.getLogger("business_spa.outbox")

BATCH_LIMIT = 50


async def handler(ctx: ActionContext) -> None:
    db = await ctx.get_db()

    pending = await db.outbox.find({"status": "pending"}).to_list(length=BATCH_LIMIT)
    if not pending:
        return

    logger.info("outbox-dispatch: draining %d pending message(s)", len(pending))

    for msg in pending:
        msg_id = msg.get("_id")
        channel = msg.get("channel")
        to = msg.get("to")
        template = msg.get("template")

        try:
            # ================================================================
            # HOOK: replace this block with a real provider call.
            #
            # Example shapes (not implemented here):
            #   if channel == "email":
            #       await resend_client.emails.send(to=to, template=template, ...)
            #   elif channel == "whatsapp":
            #       await twilio_client.messages.create_async(...)
            #
            # Until that's wired, we just log the payload so the pipeline
            # is observable from the admin UI.
            # ================================================================
            logger.info(
                "[outbox] channel=%s to=%s template=%s payload=%s",
                channel,
                to,
                template,
                msg.get("payload"),
            )

            now_iso = datetime.now(tz=timezone.utc).isoformat()
            await db.outbox.update_one(
                {"_id": msg_id},
                {
                    "$set": {
                        "status": "logged",
                        "delivered_at": now_iso,
                        "error": None,
                    },
                    "$inc": {"attempts": 1},
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("outbox-dispatch: failed to process message %s", msg_id)
            await db.outbox.update_one(
                {"_id": msg_id},
                {
                    "$set": {"status": "failed", "error": str(exc)},
                    "$inc": {"attempts": 1},
                },
            )
