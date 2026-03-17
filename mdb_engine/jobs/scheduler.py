"""
Manifest-driven scheduled jobs.

Reads the ``jobs`` section from a manifest and registers each job as a
recurring task using the existing ``mdb_engine.tasks`` infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

_CRON_SHORTCUTS: dict[str, int] = {
    "@hourly": 3600,
    "@daily": 86400,
    "@weekly": 604800,
}


def parse_cron_to_seconds(schedule: str) -> int:
    """Parse a cron expression or shortcut into an interval in seconds.

    Supports:
    - Shortcuts: ``@hourly``, ``@daily``, ``@weekly``
    - Simple interval strings: ``30m``, ``6h``, ``1d``
    - Standard 5-field cron: only the minute field is used to compute
      a rough interval (e.g. ``0 3 * * *`` -> every 24h).
    """
    schedule = schedule.strip()
    if schedule in _CRON_SHORTCUTS:
        return _CRON_SHORTCUTS[schedule]

    if schedule[-1] in ("s", "m", "h", "d"):
        unit = schedule[-1]
        try:
            val = int(schedule[:-1])
        except ValueError:
            return 86400
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return val * multipliers.get(unit, 1)

    parts = schedule.split()
    if len(parts) == 5:
        minute, hour = parts[0], parts[1]
        if hour != "*" and minute != "*":
            return 86400
        if hour == "*" and minute != "*":
            return 3600
        return 86400

    return 86400


class ManifestJobScheduler:
    """Register and run manifest-declared scheduled jobs."""

    def __init__(self, jobs_config: dict[str, dict[str, Any]], db: Any) -> None:
        self._jobs = jobs_config
        self._db = db
        self._handles: list[asyncio.Task] = []

    def start(self) -> list[asyncio.Task]:
        """Start all jobs as asyncio tasks."""
        for name, config in self._jobs.items():
            interval = parse_cron_to_seconds(config.get("schedule", "@daily"))
            handle = asyncio.create_task(
                self._run_loop(name, config, interval),
                name=f"mdb-job-{name}",
            )
            self._handles.append(handle)
            logger.info("Started scheduled job '%s' (every %ds)", name, interval)
        return self._handles

    async def stop(self) -> None:
        """Cancel all running job tasks."""
        for h in self._handles:
            if not h.done():
                h.cancel()
                try:
                    await h
                except asyncio.CancelledError:
                    pass
        logger.info("All scheduled jobs stopped")

    async def _run_loop(self, name: str, config: dict[str, Any], interval: int) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                await self._execute_job(name, config)
            except asyncio.CancelledError:
                break
            except (PyMongoError, OSError, KeyError, ValueError, TypeError, RuntimeError):
                logger.exception("Scheduled job '%s' failed", name)

    async def _execute_job(self, name: str, config: dict[str, Any]) -> None:
        """Execute a single job iteration."""
        action = config.get("action", "update")
        collection_name = config.get("collection", "")
        if not collection_name:
            return

        collection = self._db[collection_name]
        now = datetime.now(timezone.utc)

        filter_spec = _resolve_time_vars(config.get("filter", {}), now)
        update_spec = config.get("update", {})

        if action == "update":
            result = await collection.update_many(filter_spec, update_spec)
            logger.info(
                "Job '%s' updated %d docs in %s",
                name,
                result.modified_count,
                collection_name,
            )
        elif action == "delete":
            result = await collection.delete_many(filter_spec)
            logger.info(
                "Job '%s' deleted %d docs from %s",
                name,
                result.deleted_count,
                collection_name,
            )


def _resolve_time_vars(obj: Any, now: datetime) -> Any:
    """Replace time variable placeholders like $$NOW_MINUS_90D."""
    if isinstance(obj, str):
        if obj == "$$NOW":
            return now
        if obj.startswith("$$NOW_MINUS_"):
            return _compute_time_offset(obj, now)
        return obj
    if isinstance(obj, dict):
        return {k: _resolve_time_vars(v, now) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_time_vars(item, now) for item in obj]
    return obj


def _compute_time_offset(var: str, now: datetime) -> datetime:
    """Parse $$NOW_MINUS_90D style variables."""
    from datetime import timedelta

    suffix = var[len("$$NOW_MINUS_") :]
    unit = suffix[-1].upper()
    try:
        val = int(suffix[:-1])
    except (ValueError, IndexError):
        return now
    if unit == "D":
        return now - timedelta(days=val)
    if unit == "H":
        return now - timedelta(hours=val)
    if unit == "M":
        return now - timedelta(minutes=val)
    return now
