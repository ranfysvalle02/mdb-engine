"""
Declarative lifecycle hooks for auto-CRUD collections.

Hooks are manifest-driven side effects that fire after create, update, or
delete operations.  They are **fire-and-forget** — errors are logged but
never fail the originating request.

Supported actions:

* ``insert`` — insert a document into another collection.
* ``update`` — run ``$set`` on documents in another collection.

All template placeholders (``{{user.*}}``, ``{{doc.*}}``, ``$$NOW``) are
resolved before execution.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from .template_resolver import resolve_template

logger = logging.getLogger(__name__)


class HookExecutor:
    """Execute manifest-declared hooks after CRUD operations."""

    def __init__(self, hooks_config: dict[str, list[dict[str, Any]]]) -> None:
        self._config = hooks_config

    async def run(
        self,
        event: str,
        doc: dict[str, Any],
        user: dict[str, Any] | None,
        db: Any,
    ) -> None:
        """Run all hooks registered for *event*.

        Args:
            event: One of ``after_create``, ``after_update``, ``after_delete``.
            doc: The document that was just created/updated/deleted (serialised).
            user: The authenticated user (or ``None``).
            db: Scoped database wrapper for writing side effects.
        """
        actions = self._config.get(event, [])
        if not actions:
            return

        for action_def in actions:
            try:
                await self._execute_action(action_def, doc, user, db)
            except (PyMongoError, HTTPException, KeyError, ValueError, TypeError):
                logger.exception(
                    "Hook %s action failed (collection=%s, action=%s)",
                    event,
                    action_def.get("collection", "?"),
                    action_def.get("action", "?"),
                )

    async def _execute_action(
        self,
        action_def: dict[str, Any],
        doc: dict[str, Any],
        user: dict[str, Any] | None,
        db: Any,
    ) -> None:
        action = action_def.get("action")
        target_collection = action_def.get("collection")
        if not action or not target_collection:
            return

        if action == "insert":
            document = action_def.get("document", {})
            resolved = resolve_template(document, user, doc=doc)
            collection = db[target_collection]
            await collection.insert_one(resolved)

        elif action == "update":
            filter_spec = action_def.get("filter", {})
            update_spec = action_def.get("update", {})
            resolved_filter = resolve_template(filter_spec, user, doc=doc)
            resolved_update = resolve_template(update_spec, user, doc=doc)
            collection = db[target_collection]
            await collection.update_many(resolved_filter, {"$set": resolved_update})
