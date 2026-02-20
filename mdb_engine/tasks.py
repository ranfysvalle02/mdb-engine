"""
Managed background tasks for mdb-engine apps.

Provides a ``@recurring_task`` decorator that the engine registers at
startup, runs with exponential backoff on failure, and exposes status
via the health endpoint.

Usage::

    from mdb_engine.tasks import recurring_task

    @recurring_task(interval_seconds=3600, name="cleanup-expired")
    async def cleanup(db):
        await db.sessions.delete_many({"expires_at": {"$lt": datetime.utcnow()}})
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskStatus:
    name: str
    interval_seconds: float
    last_run: float | None = None
    last_error: str | None = None
    run_count: int = 0
    error_count: int = 0
    running: bool = False


@dataclass
class _TaskDef:
    func: Callable[..., Coroutine]
    interval_seconds: float
    name: str
    max_backoff: float = 300.0  # 5 min ceiling
    status: TaskStatus = field(init=False)
    handle: asyncio.Task | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.status = TaskStatus(name=self.name, interval_seconds=self.interval_seconds)


# Global registry so the engine can discover tasks at startup
_registry: list[_TaskDef] = []


def recurring_task(
    interval_seconds: float,
    name: str | None = None,
    max_backoff: float = 300.0,
) -> Callable:
    """Decorator that registers an async function as a recurring background task.

    The decorated function can accept zero arguments, or a ``db`` keyword
    argument (which will receive a scoped database wrapper if available).
    """

    def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
        task_name = name or func.__name__
        task_def = _TaskDef(
            func=func,
            interval_seconds=interval_seconds,
            name=task_name,
            max_backoff=max_backoff,
        )
        _registry.append(task_def)
        return func

    return decorator


async def _run_task_loop(task: _TaskDef, kwargs: dict[str, Any]) -> None:
    """Internal loop that runs a task forever with exponential backoff."""
    backoff = task.interval_seconds
    while True:
        try:
            task.status.running = True
            await task.func(**kwargs)
            task.status.run_count += 1
            task.status.last_run = time.time()
            task.status.last_error = None
            backoff = task.interval_seconds  # reset on success
        except asyncio.CancelledError:
            logger.info("Task '%s' cancelled", task.name)
            break
        except Exception as exc:  # noqa: BLE001
            task.status.error_count += 1
            task.status.last_error = str(exc)
            logger.exception("Task '%s' failed: %s (retry in %.0fs)", task.name, exc, backoff)
            backoff = min(backoff * 2, task.max_backoff)
        finally:
            task.status.running = False

        await asyncio.sleep(backoff)


def start_all_tasks(**kwargs: Any) -> list[asyncio.Task]:
    """Start all registered recurring tasks.  Call during app startup."""
    handles = []
    for task_def in _registry:
        handle = asyncio.create_task(
            _run_task_loop(task_def, kwargs),
            name=f"mdb-task-{task_def.name}",
        )
        task_def.handle = handle
        handles.append(handle)
        logger.info("Started recurring task '%s' (every %ss)", task_def.name, task_def.interval_seconds)
    return handles


async def stop_all_tasks() -> None:
    """Gracefully cancel all running tasks.  Call during app shutdown."""
    for task_def in _registry:
        if task_def.handle and not task_def.handle.done():
            task_def.handle.cancel()
            try:
                await task_def.handle
            except asyncio.CancelledError:
                pass
    logger.info("All recurring tasks stopped")


def get_task_statuses() -> list[dict[str, Any]]:
    """Return status dicts for all registered tasks (for health endpoints)."""
    return [
        {
            "name": t.status.name,
            "interval_seconds": t.status.interval_seconds,
            "last_run": t.status.last_run,
            "last_error": t.status.last_error,
            "run_count": t.status.run_count,
            "error_count": t.status.error_count,
            "running": t.status.running,
        }
        for t in _registry
    ]
