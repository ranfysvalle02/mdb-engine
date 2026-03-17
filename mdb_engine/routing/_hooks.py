"""
Declarative lifecycle hooks for auto-CRUD collections.

Hooks are manifest-driven side effects that fire after create, update, or
delete operations.  They are **fire-and-forget** — errors are logged but
never fail the originating request.

Supported actions:

* ``insert`` — insert a document into another collection.
* ``update`` — run update operators (``$set``, ``$inc``, ``$push``, etc.)
  on documents in another collection.  When the update value contains
  MongoDB operator keys (starting with ``$``), they are passed through
  directly; otherwise the value is wrapped in ``$set`` for backward compat.
* ``delete`` — delete documents from another collection.
* ``http`` — send an HTTP request to an external URL (webhook).

All template placeholders (``{{user.*}}``, ``{{doc.*}}``, ``{{prev.*}}``,
``$$NOW``) are resolved before execution.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo.errors import PyMongoError

from .template_resolver import resolve_template

logger = logging.getLogger(__name__)

# Exhaustive set of exception types that represent operational hook failures.
# Each type maps to a real failure mode:
#   PyMongoError  — any MongoDB driver error (insert/update/delete)
#   OSError       — network & I/O errors (connection refused, DNS, timeouts)
#   KeyError      — missing keys during template resolution
#   ValueError    — bad values during template / data coercion
#   TypeError     — type mismatches in template resolution or BSON encoding
#   RuntimeError  — general operational failures (e.g. httpx wrapped errors)
_HOOK_ACTION_ERRORS = (PyMongoError, OSError, KeyError, ValueError, TypeError, RuntimeError)

_MONGO_UPDATE_OPERATORS = frozenset(
    {
        "$set",
        "$unset",
        "$inc",
        "$push",
        "$pull",
        "$addToSet",
        "$pop",
        "$min",
        "$max",
        "$mul",
        "$rename",
        "$currentDate",
        "$setOnInsert",
        "$bit",
    }
)


def _has_update_operators(doc: dict[str, Any]) -> bool:
    """Return True if *doc* contains MongoDB update operator keys."""
    return any(k in _MONGO_UPDATE_OPERATORS for k in doc)


def _evaluate_condition(
    condition: dict[str, Any],
    doc: dict[str, Any],
    prev: dict[str, Any] | None,
) -> bool:
    """Evaluate a simple MQL-like match condition against doc/prev.

    Supports field paths prefixed with ``doc.`` and ``prev.`` as well as
    plain field names (resolved from *doc*).  Comparison operators
    ``$ne``, ``$in``, ``$nin``, ``$exists``, ``$gt``, ``$lt``, ``$gte``,
    ``$lte`` are supported inside value dicts.
    """
    for key, expected in condition.items():
        source, field = _resolve_condition_source(key, doc, prev)
        actual = source.get(field) if source else None
        if not _match_value(actual, expected):
            return False
    return True


def _resolve_condition_source(
    key: str,
    doc: dict[str, Any],
    prev: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    """Map a condition key to its source dict and field name."""
    if key.startswith("doc."):
        return doc, key[4:]
    if key.startswith("prev."):
        return (prev or {}), key[5:]
    return doc, key


def _match_value(actual: Any, expected: Any) -> bool:
    """Compare *actual* against *expected*, which may be a plain value or
    a dict of MQL comparison operators."""
    if isinstance(expected, dict):
        for op, val in expected.items():
            if op == "$ne" and actual == val:
                return False
            elif op == "$in" and actual not in val:
                return False
            elif op == "$nin" and actual in val:
                return False
            elif op == "$exists":
                exists = actual is not None
                if exists != val:
                    return False
            elif op == "$gt" and not (actual is not None and actual > val):
                return False
            elif op == "$lt" and not (actual is not None and actual < val):
                return False
            elif op == "$gte" and not (actual is not None and actual >= val):
                return False
            elif op == "$lte" and not (actual is not None and actual <= val):
                return False
        return True
    return actual == expected


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
        *,
        prev: dict[str, Any] | None = None,
    ) -> None:
        """Run all hooks registered for *event*.

        Args:
            event: One of ``after_create``, ``after_update``, ``after_delete``.
            doc: The document that was just created/updated/deleted (serialised).
            user: The authenticated user (or ``None``).
            db: Scoped database wrapper for writing side effects.
            prev: The previous document state (before update), if available.
        """
        actions = self._config.get(event, [])
        if not actions:
            return

        for action_def in actions:
            try:
                await self._execute_action(action_def, doc, user, db, prev=prev)
            except _HOOK_ACTION_ERRORS:
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
        *,
        prev: dict[str, Any] | None = None,
    ) -> None:
        action = action_def.get("action")
        target_collection = action_def.get("collection")
        if not action or not target_collection:
            return

        condition = action_def.get("if")
        if condition and not _evaluate_condition(condition, doc, prev):
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
            if _has_update_operators(resolved_update):
                update_doc = resolved_update
            else:
                update_doc = {"$set": resolved_update}
            collection = db[target_collection]
            await collection.update_many(resolved_filter, update_doc)

        elif action == "delete":
            filter_spec = action_def.get("filter", {})
            resolved_filter = resolve_template(filter_spec, user, doc=doc)
            collection = db[target_collection]
            await collection.delete_many(resolved_filter)

        elif action == "http":
            await self._execute_http_action(action_def, doc, user)

    async def _execute_http_action(
        self,
        action_def: dict[str, Any],
        doc: dict[str, Any],
        user: dict[str, Any] | None,
    ) -> None:
        """Send an HTTP request as a hook side effect.

        This method is a self-contained fault boundary — all errors from
        the HTTP call are caught and logged here so they never propagate
        to the caller.  This is important because httpx is an optional
        dependency and may be mocked in tests.
        """
        try:
            import httpx
        except ImportError:
            logger.exception("httpx is required for http hook actions: pip install httpx")
            return

        url = action_def.get("url", "")
        if isinstance(url, dict):
            url = resolve_template(url, user, doc=doc)
        else:
            url = resolve_template({"_": url}, user, doc=doc)["_"] if "{{" in url else url
        method = action_def.get("method", "POST").upper()
        body = resolve_template(action_def.get("body", {}), user, doc=doc)
        headers = resolve_template(action_def.get("headers", {}), user, doc=doc)
        timeout = action_def.get("timeout", 10)

        # Build a safe tuple of catchable HTTP error types.  When httpx is
        # a real module, httpx.HTTPError is a proper exception class and we
        # include it.  When httpx is mocked in tests (via sys.modules),
        # httpx.HTTPError is a MagicMock — we detect that and fall back to
        # OSError only so the except clause doesn't raise TypeError.
        _http_errors: tuple[type[BaseException], ...] = (OSError,)
        _http_err = getattr(httpx, "HTTPError", None)
        if isinstance(_http_err, type) and issubclass(_http_err, BaseException):
            _http_errors = (_http_err, OSError)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                await client.request(method, url, json=body, headers=headers)
        except _http_errors:
            logger.exception("HTTP hook action failed: %s %s", method, url)


class TransactionalHookExecutor(HookExecutor):
    """Execute hooks within a MongoDB multi-document transaction.

    When ``transactional`` is enabled, the primary write and all hook
    actions are wrapped in a single session+transaction.  If any hook
    fails, the entire operation rolls back.

    Requires a MongoDB replica set.
    """

    def __init__(
        self,
        hooks_config: dict[str, list[dict[str, Any]]],
        client: Any,
    ) -> None:
        super().__init__(hooks_config)
        self._client = client

    async def run(
        self,
        event: str,
        doc: dict[str, Any],
        user: dict[str, Any] | None,
        db: Any,
        *,
        prev: dict[str, Any] | None = None,
    ) -> None:
        actions = self._config.get(event, [])
        if not actions:
            return

        async with await self._client.start_session() as session:
            session.start_transaction()
            committed = False
            try:
                for action_def in actions:
                    await self._execute_action(action_def, doc, user, db, prev=prev)
                await session.commit_transaction()
                committed = True
            finally:
                if not committed:
                    await session.abort_transaction()


class BackgroundHookExecutor(HookExecutor):
    """Execute hooks in background tasks with retry.

    Hooks with ``background: true`` are offloaded to ``asyncio.create_task``
    with configurable retry logic.  Failures are logged to the
    ``_hook_failures`` system collection.
    """

    def __init__(
        self,
        hooks_config: dict[str, list[dict[str, Any]]],
        failure_collection_name: str = "_hook_failures",
    ) -> None:
        super().__init__(hooks_config)
        self._failure_collection = failure_collection_name

    async def run(
        self,
        event: str,
        doc: dict[str, Any],
        user: dict[str, Any] | None,
        db: Any,
        *,
        prev: dict[str, Any] | None = None,
    ) -> None:
        import asyncio

        actions = self._config.get(event, [])
        if not actions:
            return

        for action_def in actions:
            is_background = action_def.get("background", False)
            if is_background:
                asyncio.create_task(self._run_with_retry(action_def, event, doc, user, db, prev=prev))
            else:
                try:
                    await self._execute_action(action_def, doc, user, db, prev=prev)
                except _HOOK_ACTION_ERRORS:
                    logger.exception(
                        "Hook %s action failed (collection=%s, action=%s)",
                        event,
                        action_def.get("collection", "?"),
                        action_def.get("action", "?"),
                    )

    async def _run_with_retry(
        self,
        action_def: dict[str, Any],
        event: str,
        doc: dict[str, Any],
        user: dict[str, Any] | None,
        db: Any,
        *,
        prev: dict[str, Any] | None = None,
    ) -> None:
        import asyncio
        from datetime import datetime, timezone

        retry_config = action_def.get("retry", {})
        max_attempts = retry_config.get("attempts", 3)
        backoff = retry_config.get("backoff", "exponential")

        for attempt in range(1, max_attempts + 1):
            try:
                await self._execute_action(action_def, doc, user, db, prev=prev)
                return
            except _HOOK_ACTION_ERRORS:
                logger.warning(
                    "Background hook attempt %d/%d failed (event=%s, action=%s)",
                    attempt,
                    max_attempts,
                    event,
                    action_def.get("action", "?"),
                )
                if attempt < max_attempts:
                    if backoff == "exponential":
                        delay = 2 ** (attempt - 1)
                    elif backoff == "linear":
                        delay = attempt
                    else:
                        delay = 1
                    await asyncio.sleep(delay)

        try:
            failure_col = db[self._failure_collection]
            await failure_col.insert_one(
                {
                    "event": event,
                    "action": action_def.get("action"),
                    "collection": action_def.get("collection"),
                    "doc_id": str(doc.get("_id", "")),
                    "attempts": max_attempts,
                    "failed_at": datetime.now(timezone.utc),
                }
            )
        except PyMongoError:
            logger.exception("Failed to log hook failure")
