"""
Action discovery and mounting.

Scans an ``actions/`` directory for single-file Python handlers, merges
file-level metadata with manifest ``actions`` config, and mounts HTTP
routes / registers scheduled & event actions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..dependencies import require_role, require_user
from . import ActionContext

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..core.engine import MongoDBEngine

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
MAX_TIMEOUT = 300


@dataclass
class ActionDef:
    """Parsed definition of a single action."""

    name: str
    handler: Callable[..., Coroutine]
    trigger: str = "http"
    method: str = "POST"
    auth_required: bool = False
    auth_roles: list[str] = field(default_factory=list)
    timeout: float = DEFAULT_TIMEOUT
    schedule: str = ""
    interval_seconds: float = 0
    event: str = ""
    collection: str = ""
    source_path: str = ""


# ------------------------------------------------------------------
# Module-level action registry for event triggers.
# Populated by register_event_actions(), read by HookExecutor.
# Stores (ActionDef, engine, slug) tuples.
# ------------------------------------------------------------------

_action_registry: dict[str, tuple[ActionDef, Any, str]] = {}


def get_registered_action(name: str) -> tuple[ActionDef | None, Any, str]:
    """Look up a registered event-trigger action by name.

    Returns ``(action_def, engine, slug)`` or ``(None, None, "")`` if
    not found.
    """
    entry = _action_registry.get(name)
    if entry is None:
        return None, None, ""
    return entry


def _clear_registry() -> None:
    """Clear the action registry (used in tests)."""
    _action_registry.clear()


# ------------------------------------------------------------------
# Discovery
# ------------------------------------------------------------------


def _import_action_module(
    file_path: Path,
    slug: str,
) -> Any | None:
    """Import a single action file and return the module, or None on failure."""
    module_name = f"mdb_action_{slug}_{file_path.stem}_{id(file_path)}"

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        logger.warning("Could not create module spec for action '%s'", file_path)
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    parent_dir = str(file_path.parent.resolve())
    path_inserted = parent_dir not in sys.path
    if path_inserted:
        sys.path.insert(0, parent_dir)

    try:
        spec.loader.exec_module(module)
    except SyntaxError:
        logger.exception("Syntax error in action file '%s', skipping", file_path)
        return None
    except Exception:
        logger.exception("Failed to import action file '%s', skipping", file_path)
        return None
    finally:
        if path_inserted and parent_dir in sys.path:
            sys.path.remove(parent_dir)

    return module


def discover_actions(
    actions_dir: Path,
    actions_config: dict[str, dict[str, Any]] | None = None,
    *,
    slug: str = "",
) -> list[ActionDef]:
    """Scan ``actions/`` for handler files, merge with manifest config.

    Returns a list of :class:`ActionDef` instances ready for mounting.
    """
    actions_config = actions_config or {}
    discovered: list[ActionDef] = []

    for py_file in sorted(actions_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        module = _import_action_module(py_file, slug)
        if module is None:
            continue

        handler = getattr(module, "handler", None)
        if handler is None or not asyncio.iscoroutinefunction(handler):
            logger.warning(
                "Action file '%s' has no async handler() function, skipping",
                py_file.name,
            )
            continue

        action_name = py_file.stem

        # Module-level metadata (defaults)
        mod_trigger = getattr(module, "__trigger__", "http")
        mod_method = getattr(module, "__method__", "POST")
        mod_timeout = getattr(module, "__timeout__", DEFAULT_TIMEOUT)
        mod_schedule = getattr(module, "__schedule__", "")
        mod_interval = getattr(module, "__interval_seconds__", 0)
        mod_event = getattr(module, "__event__", "")
        mod_collection = getattr(module, "__collection__", "")
        mod_auth: dict[str, Any] = getattr(module, "__auth__", {})

        # Manifest config overrides module metadata
        cfg = actions_config.get(action_name, {})

        trigger = cfg.get("trigger", mod_trigger)
        method = cfg.get("method", mod_method).upper()
        timeout = cfg.get("timeout", mod_timeout)
        schedule = cfg.get("schedule", mod_schedule)
        interval_seconds = cfg.get("interval_seconds", mod_interval)
        event = cfg.get("event", mod_event)
        collection = cfg.get("collection", mod_collection)

        auth_cfg = cfg.get("auth", mod_auth)
        auth_required = bool(auth_cfg.get("required", False))
        auth_roles = list(auth_cfg.get("roles", []))

        timeout = max(1, min(float(timeout), MAX_TIMEOUT))

        action_def = ActionDef(
            name=action_name,
            handler=handler,
            trigger=trigger,
            method=method,
            auth_required=auth_required,
            auth_roles=auth_roles,
            timeout=timeout,
            schedule=schedule,
            interval_seconds=interval_seconds,
            event=event,
            collection=collection,
            source_path=str(py_file),
        )
        discovered.append(action_def)
        logger.debug("Discovered action '%s' (trigger=%s) from %s", action_name, trigger, py_file.name)

    return discovered


# ------------------------------------------------------------------
# HTTP mounting
# ------------------------------------------------------------------


def mount_http_actions(
    app: FastAPI,
    action_defs: list[ActionDef],
    engine: MongoDBEngine,
    slug: str,
    *,
    app_auth_enabled: bool = False,
) -> int:
    """Mount HTTP-triggered actions as ``/actions/v1/<name>`` routes.

    Returns the number of routes mounted.
    """
    count = 0
    for adef in action_defs:
        if adef.trigger != "http":
            continue

        dependencies: list[Any] = []
        if adef.auth_roles:
            dependencies.append(Depends(require_role(*adef.auth_roles)))
        elif adef.auth_required or app_auth_enabled:
            dependencies.append(Depends(require_user()))

        _handler = adef.handler
        _timeout = adef.timeout
        _engine = engine
        _slug = slug
        _name = adef.name

        async def _endpoint(
            request: Request,
            *,
            _h: Any = _handler,
            _t: float = _timeout,
            _e: Any = _engine,
            _s: str = _slug,
            _n: str = _name,
        ) -> Any:
            ctx = ActionContext(engine=_e, slug=_s, request=request)
            try:
                result = await asyncio.wait_for(_h(ctx), timeout=_t)
            except asyncio.TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail=f"Action '{_n}' timed out after {_t}s",
                ) from exc
            if result is None:
                return JSONResponse(content={"ok": True})
            if isinstance(result, dict):
                return JSONResponse(content=result)
            return result

        path = f"/actions/v1/{adef.name}"
        app.add_api_route(
            path,
            _endpoint,
            methods=[adef.method],
            tags=["actions"],
            summary=f"Action: {adef.name}",
            dependencies=dependencies,
        )
        count += 1
        _auth_label = ""
        if adef.auth_roles:
            _auth_label = f" (roles: {', '.join(adef.auth_roles)})"
        elif adef.auth_required or app_auth_enabled:
            _auth_label = " (auth required)"
        logger.info("Mounted action '%s' at %s %s%s", adef.name, adef.method, path, _auth_label)

    return count


# ------------------------------------------------------------------
# Event action registration
# ------------------------------------------------------------------


def register_event_actions(
    action_defs: list[ActionDef],
    engine: MongoDBEngine,
    slug: str,
) -> int:
    """Register event-triggered actions in the module-level registry.

    Returns the number of actions registered.
    """
    count = 0
    for adef in action_defs:
        if adef.trigger != "event":
            continue
        if not adef.event or not adef.collection:
            logger.warning(
                "Event action '%s' missing 'event' or 'collection', skipping",
                adef.name,
            )
            continue
        _action_registry[adef.name] = (adef, engine, slug)
        count += 1
        logger.info(
            "Registered event action '%s' for %s on '%s'",
            adef.name,
            adef.event,
            adef.collection,
        )
    return count


def inject_event_actions_into_collections(
    action_defs: list[ActionDef],
    collections_config: dict[str, dict[str, Any]],
) -> None:
    """Inject event actions as hook entries in their target collection configs.

    This must be called **before** ``mount_auto_crud_routes`` so that
    the auto-CRUD router picks up the hook entries.
    """
    for adef in action_defs:
        if adef.trigger != "event":
            continue
        if not adef.event or not adef.collection:
            continue

        col_cfg = collections_config.get(adef.collection)
        if col_cfg is None:
            col_cfg = {}
            collections_config[adef.collection] = col_cfg

        hooks = col_cfg.setdefault("hooks", {})
        event_hooks = hooks.setdefault(adef.event, [])
        event_hooks.append(
            {
                "action": "run_action",
                "action_name": adef.name,
                "collection": adef.collection,
            }
        )
        logger.debug(
            "Injected event action '%s' into %s.hooks.%s",
            adef.name,
            adef.collection,
            adef.event,
        )


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


def mount_actions(
    app: FastAPI,
    actions_dir: Path,
    actions_config: dict[str, dict[str, Any]] | None,
    engine: MongoDBEngine,
    slug: str,
    *,
    app_auth_enabled: bool = False,
    collections_config: dict[str, dict[str, Any]] | None = None,
) -> list[ActionDef]:
    """Discover, register, and mount all actions.

    This is the main entry point called from ``create_app`` and the
    CLI serve modules.

    Returns the full list of discovered action definitions.
    """
    action_defs = discover_actions(actions_dir, actions_config, slug=slug)
    if not action_defs:
        return []

    # HTTP actions
    http_count = mount_http_actions(
        app,
        action_defs,
        engine,
        slug,
        app_auth_enabled=app_auth_enabled,
    )

    # Event actions — register + inject into collection hooks
    event_count = register_event_actions(action_defs, engine, slug)
    if event_count and collections_config is not None:
        inject_event_actions_into_collections(action_defs, collections_config)

    # Scheduled actions are registered separately via scheduler.py
    # and started during lifespan.
    from .scheduler import register_scheduled_actions

    sched_count = register_scheduled_actions(action_defs, engine, slug)

    total = http_count + event_count + sched_count
    logger.info(
        "Actions for '%s': %d HTTP, %d scheduled, %d event (%d total)",
        slug,
        http_count,
        sched_count,
        event_count,
        total,
    )
    return action_defs
