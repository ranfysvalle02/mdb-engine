"""
Scheduled action support.

Registers ``trigger="schedule"`` actions into the ``mdb_engine.tasks``
recurring-task loop.  Supports both ``interval_seconds`` (simple) and
``schedule`` (cron expression, requires ``croniter``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import ActionContext

if TYPE_CHECKING:
    from ..core.engine import MongoDBEngine
    from .discovery import ActionDef

logger = logging.getLogger(__name__)


def _parse_cron_interval(expression: str) -> float | None:
    """Convert a cron expression to an approximate interval in seconds.

    Returns ``None`` if ``croniter`` is not installed.
    """
    try:
        from croniter import croniter
    except ImportError:
        logger.warning(
            "croniter is not installed — cron expressions are not supported. " "Install with: pip install croniter"
        )
        return None

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    cron = croniter(expression, now)
    next_1 = cron.get_next(datetime)
    next_2 = cron.get_next(datetime)
    return (next_2 - next_1).total_seconds()


# ------------------------------------------------------------------
# Internal registry for scheduled actions (separate from tasks._registry
# so we can start/stop them independently).
# ------------------------------------------------------------------


@dataclass
class _ScheduledActionStatus:
    name: str
    interval_seconds: float
    last_run: float | None = None
    last_error: str | None = None
    run_count: int = 0
    error_count: int = 0
    running: bool = False


@dataclass
class _ScheduledActionDef:
    name: str
    handler: Any
    engine: Any
    slug: str
    interval_seconds: float
    max_backoff: float = 300.0
    status: _ScheduledActionStatus = field(init=False)
    handle: asyncio.Task | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.status = _ScheduledActionStatus(
            name=self.name,
            interval_seconds=self.interval_seconds,
        )


_scheduled_registry: list[_ScheduledActionDef] = []


def register_scheduled_actions(
    action_defs: list[ActionDef],
    engine: MongoDBEngine,
    slug: str,
) -> int:
    """Register schedule-triggered actions for later startup.

    Returns the number of actions registered.
    """
    count = 0
    for adef in action_defs:
        if adef.trigger != "schedule":
            continue

        interval = adef.interval_seconds
        if not interval and adef.schedule:
            interval = _parse_cron_interval(adef.schedule) or 0

        if not interval or interval <= 0:
            logger.warning(
                "Scheduled action '%s' has no valid interval or cron schedule, skipping",
                adef.name,
            )
            continue

        sdef = _ScheduledActionDef(
            name=adef.name,
            handler=adef.handler,
            engine=engine,
            slug=slug,
            interval_seconds=float(interval),
        )
        _scheduled_registry.append(sdef)
        count += 1
        logger.info(
            "Registered scheduled action '%s' (every %.0fs)",
            adef.name,
            interval,
        )
    return count


async def _run_scheduled_loop(sdef: _ScheduledActionDef) -> None:
    """Run a scheduled action on a loop with exponential backoff on failure."""
    backoff = sdef.interval_seconds
    while True:
        try:
            sdef.status.running = True
            ctx = ActionContext(engine=sdef.engine, slug=sdef.slug)
            await sdef.handler(ctx)
            sdef.status.run_count += 1
            sdef.status.last_run = time.time()
            sdef.status.last_error = None
            backoff = sdef.interval_seconds
        except asyncio.CancelledError:
            logger.info("Scheduled action '%s' cancelled", sdef.name)
            break
        except Exception as exc:  # noqa: BLE001
            sdef.status.error_count += 1
            sdef.status.last_error = str(exc)
            logger.exception(
                "Scheduled action '%s' failed: %s (retry in %.0fs)",
                sdef.name,
                exc,
                backoff,
            )
            backoff = min(backoff * 2, sdef.max_backoff)
        finally:
            sdef.status.running = False

        await asyncio.sleep(backoff)


def start_scheduled_actions() -> list[asyncio.Task]:
    """Start all registered scheduled actions.  Call during app lifespan."""
    handles: list[asyncio.Task] = []
    for sdef in _scheduled_registry:
        handle = asyncio.create_task(
            _run_scheduled_loop(sdef),
            name=f"mdb-action-{sdef.name}",
        )
        sdef.handle = handle
        handles.append(handle)
        logger.info(
            "Started scheduled action '%s' (every %.0fs)",
            sdef.name,
            sdef.interval_seconds,
        )
    return handles


async def stop_scheduled_actions() -> None:
    """Gracefully cancel all scheduled actions.  Call during shutdown."""
    for sdef in _scheduled_registry:
        if sdef.handle and not sdef.handle.done():
            sdef.handle.cancel()
            try:
                await sdef.handle
            except asyncio.CancelledError:
                pass
    logger.info("All scheduled actions stopped")


def get_scheduled_action_statuses() -> list[dict[str, Any]]:
    """Return status dicts for health endpoints."""
    return [
        {
            "name": s.status.name,
            "interval_seconds": s.status.interval_seconds,
            "last_run": s.status.last_run,
            "last_error": s.status.last_error,
            "run_count": s.status.run_count,
            "error_count": s.status.error_count,
            "running": s.status.running,
        }
        for s in _scheduled_registry
    ]


def _clear_scheduled_registry() -> None:
    """Clear the scheduled registry (used in tests)."""
    _scheduled_registry.clear()
